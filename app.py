from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Generator

import gradio as gr

from document_processor import extract_comments, extract_text
from llm_client import get_available_models, stream_analysis, stream_answer
from report_generator import save_analysis_to_docx, save_qa_to_docx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

_POSITIVE_WORDS = {"yes", "y", "approve", "approved", "accept", "accepted", "good", "ok", "okay", "save"}
_NEGATIVE_WORDS = {"no", "n", "reject", "rejected", "retry", "redo", "again", "bad", "nope"}

# ---------------------------------------------------------------------------
# Helper: resolve file path from Gradio upload (handles str or legacy object)
# ---------------------------------------------------------------------------

def _path(f) -> str | None:
    if f is None:
        return None
    if isinstance(f, str):
        return f
    if hasattr(f, "name"):
        return f.name
    return str(f)


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------

def refresh_models() -> gr.Dropdown:
    models = get_available_models()
    if not models:
        return gr.Dropdown(
            choices=["(no models — is ollama running?)"],
            value="(no models — is ollama running?)",
        )
    return gr.Dropdown(choices=models, value=models[0])


# ---------------------------------------------------------------------------
# Document loading
# ---------------------------------------------------------------------------

def load_documents(id_file, extra_file, claim_file):
    id_path = _path(id_file)
    claim_path = _path(claim_file)
    extra_path = _path(extra_file)

    if not id_path:
        return None, None, None, [], "⚠ Please upload the Invention Disclosure document."
    if not claim_path:
        return None, None, None, [], "⚠ Please upload the Patent Claim document."

    try:
        id_text = extract_text(id_path)
        claim_text = extract_text(claim_path)
        extra_text = extract_text(extra_path) if extra_path else ""
        questions = extract_comments(claim_path)

        lines = [
            "✅ Documents loaded successfully!",
            f"   • Invention Disclosure : {len(id_text):,} characters",
        ]
        if extra_text:
            lines.append(f"   • Additional Info      : {len(extra_text):,} characters")
        lines.append(f"   • Patent Claims        : {len(claim_text):,} characters")
        lines.append(f"   • Questions (comments) : {len(questions)} found")
        if not questions:
            lines.append("   ⚠ No comments found in the patent claim document.")

        return id_text, extra_text, claim_text, questions, "\n".join(lines)

    except Exception as exc:
        return None, None, None, [], f"❌ Error loading documents: {exc}"


# ---------------------------------------------------------------------------
# Analyze tab — streaming
# ---------------------------------------------------------------------------

def run_analysis_stream(
    id_text: str | None,
    extra_text: str | None,
    claim_text: str | None,
    model: str | None,
) -> Generator:
    if not id_text:
        yield "⚠ Please load documents first.", None
        return
    if not claim_text:
        yield "⚠ Patent claim text is missing. Please reload documents.", None
        return
    if not model or "no models" in (model or "").lower():
        yield "⚠ Please select a valid ollama model.", None
        return

    try:
        accumulated = ""
        for chunk in stream_analysis(id_text, claim_text, extra_text or "", model):
            accumulated += chunk
            yield accumulated, None

        # Save report after full generation
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = str(OUTPUT_DIR / f"analysis_{timestamp}.docx")
        save_analysis_to_docx(accumulated, out_path)
        yield accumulated, out_path

    except Exception as exc:
        yield f"❌ Analysis error: {exc}", None


# ---------------------------------------------------------------------------
# Verify tab — session management
# ---------------------------------------------------------------------------

def _make_session(questions: list[str], model: str, id_text: str, extra_text: str) -> dict:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return {
        "phase": "asking",
        "questions": questions,
        "current_index": 0,
        "current_question": questions[0],
        "current_answer": None,
        "approved_qa": [],
        "output_path": str(OUTPUT_DIR / f"qa_report_{timestamp}.docx"),
        "model": model,
        "id_text": id_text,
        "extra_text": extra_text,
    }


def start_verification(id_text, questions, model):
    """Initialise or reset a verification session and show the first question."""
    if not id_text:
        history = [{"role": "assistant", "content": "⚠ Please load documents first using **Load Documents**."}]
        return history, {"phase": "idle"}, None

    if not questions:
        history = [{"role": "assistant", "content": (
            "⚠ No questions (comments) found in the patent claim document.\n\n"
            "Add Word comments to your patent claim .docx — each comment becomes a verification question."
        )}]
        return history, {"phase": "idle"}, None

    if not model or "no models" in (model or "").lower():
        history = [{"role": "assistant", "content": "⚠ Please select a valid ollama model."}]
        return history, {"phase": "idle"}, None

    session = _make_session(questions, model, id_text, "")
    total = len(questions)
    first_q = questions[0]

    history = [{
        "role": "assistant",
        "content": (
            f"🚀 Verification started — **{total} question(s)** to verify.\n\n"
            f"**Question 1 of {total}:**\n\n> {first_q}\n\n"
            "Provide any additional context that might help answer this question, "
            "or just press **Submit** with an empty message to answer from the disclosure only."
        ),
    }]
    return history, session, None


def start_verification_full(id_text, extra_text, questions, model):
    """Like start_verification but also stores extra_text in session."""
    history, session, qa_path = start_verification(id_text, questions, model)
    if session.get("phase") == "asking":
        session["extra_text"] = extra_text or ""
        session["id_text"] = id_text or ""
    return history, session, qa_path


# ---------------------------------------------------------------------------
# Verify tab — streaming chat handler
# ---------------------------------------------------------------------------

def handle_chat_stream(
    message: str,
    history: list[dict],
    session: dict,
) -> Generator:
    """Generator that drives the verification state machine with streaming LLM output."""

    phase = session.get("phase", "idle")

    # ── Idle: not started yet ───────────────────────────────────────────────
    if phase == "idle":
        history = history + [{
            "role": "user", "content": message or "(empty)",
        }, {
            "role": "assistant",
            "content": "Please click **Start Verification** to begin.",
        }]
        yield history, session, None
        return

    # ── Done: all questions processed ───────────────────────────────────────
    if phase == "done":
        history = history + [{
            "role": "user", "content": message or "(empty)",
        }, {
            "role": "assistant",
            "content": "✅ Verification is complete! Download the Q&A report using the button below.",
        }]
        yield history, session, None
        return

    # ── Asking: user provides context, LLM streams an answer ────────────────
    if phase == "asking":
        user_msg = message.strip() if message else ""
        history = history + [{"role": "user", "content": user_msg or "(no additional context)"}]

        # Placeholder for streaming response
        history = history + [{"role": "assistant", "content": ""}]
        accumulated = ""

        try:
            for chunk in stream_answer(
                question=session["current_question"],
                id_text=session["id_text"],
                extra_text=session.get("extra_text", ""),
                user_context=user_msg,
                model=session["model"],
            ):
                accumulated += chunk
                history[-1] = {"role": "assistant", "content": accumulated}
                yield history, session, None

        except Exception as exc:
            history[-1] = {
                "role": "assistant",
                "content": f"❌ Error generating answer: {exc}\n\nPlease try again.",
            }
            yield history, session, None
            return

        # Append the feedback prompt to the same message
        idx = session["current_index"]
        total = len(session["questions"])
        feedback_prompt = (
            f"\n\n---\n*Answer for Question {idx + 1} of {total} — "
            "is this satisfactory?*\n"
            "Type **yes** (or y/approve) to save and continue, "
            "or **no** (or n/retry) to try again with more context."
        )
        history[-1] = {"role": "assistant", "content": accumulated + feedback_prompt}

        new_session = {
            **session,
            "phase": "waiting_feedback",
            "current_answer": accumulated,
        }
        yield history, new_session, None
        return

    # ── Waiting feedback: yes → save & advance / no → retry ────────────────
    if phase == "waiting_feedback":
        feedback = (message or "").strip().lower()

        # Treat empty message as a reminder
        if not feedback:
            history = history + [{
                "role": "assistant",
                "content": "Please type **yes** to accept the answer or **no** to retry.",
            }]
            yield history, session, None
            return

        history = history + [{"role": "user", "content": message}]
        words = set(feedback.split())

        # ── Positive feedback ───────────────────────────────────────────────
        if words & _POSITIVE_WORDS:
            new_qa = session["approved_qa"] + [(session["current_question"], session["current_answer"])]
            next_idx = session["current_index"] + 1
            questions = session["questions"]

            if next_idx >= len(questions):
                # All done — save docx
                new_session = {**session, "phase": "done", "approved_qa": new_qa}
                try:
                    save_qa_to_docx(new_qa, session["output_path"])
                    history = history + [{
                        "role": "assistant",
                        "content": (
                            f"✅ Answer saved! All **{len(new_qa)}** answer(s) approved.\n\n"
                            "🎉 Verification complete! Download the Q&A report below."
                        ),
                    }]
                    yield history, new_session, session["output_path"]
                except Exception as exc:
                    history = history + [{
                        "role": "assistant",
                        "content": f"❌ Error saving Q&A report: {exc}",
                    }]
                    yield history, new_session, None
                return

            # Move to next question
            new_session = {
                **session,
                "phase": "asking",
                "approved_qa": new_qa,
                "current_index": next_idx,
                "current_question": questions[next_idx],
                "current_answer": None,
            }
            total = len(questions)
            next_q = questions[next_idx]
            history = history + [{
                "role": "assistant",
                "content": (
                    f"✅ Answer saved! ({len(new_qa)}/{total} approved)\n\n"
                    f"**Question {next_idx + 1} of {total}:**\n\n> {next_q}\n\n"
                    "Provide any additional context, or submit with an empty message."
                ),
            }]
            yield history, new_session, None
            return

        # ── Negative feedback ───────────────────────────────────────────────
        if words & _NEGATIVE_WORDS:
            new_session = {**session, "phase": "asking", "current_answer": None}
            idx = session["current_index"]
            total = len(session["questions"])
            question = session["current_question"]
            history = history + [{
                "role": "assistant",
                "content": (
                    "🔄 No problem — let's try again.\n\n"
                    f"**Question {idx + 1} of {total}:**\n\n> {question}\n\n"
                    "Please provide more context or clarification to help generate a better answer."
                ),
            }]
            yield history, new_session, None
            return

        # ── Unrecognised ────────────────────────────────────────────────────
        history = history + [{
            "role": "assistant",
            "content": "I didn't catch that. Please type **yes** to accept or **no** to retry.",
        }]
        yield history, session, None


# ---------------------------------------------------------------------------
# Gradio UI
# ---------------------------------------------------------------------------

def _submit_wrap(message, history, session, prev_qa_path):
    """Wrap the streaming generator; preserve qa_path across turns."""
    last_history, last_session, last_qa = history, session, prev_qa_path
    for h, s, qa in handle_chat_stream(message, history, session):
        last_history, last_session, last_qa = h, s, qa or prev_qa_path
        yield "", last_history, last_session, last_qa, last_qa


_CSS = """
/* ── model dropdown: show full names, no clipping ── */
#model-selector li,
#model-selector [class*="item"] {
    white-space:   normal     !important;
    word-break:    break-word !important;
    overflow:      visible    !important;
    text-overflow: unset      !important;
    line-height:   1.35       !important;
    padding:       5px 10px   !important;
}
#model-selector [class*="options"],
#model-selector [class*="list-container"],
#model-selector ul {
    max-height: 360px !important;
    overflow-y: auto  !important;
}
/* ── upload button rows: tight vertical spacing ── */
.upload-row { gap: 6px !important; align-items: center !important; min-height: 0 !important; }
.upload-row > * { margin-bottom: 0 !important; }
"""

# ── helper used by upload event handlers ────────────────────────────────────
def _on_upload(f) -> tuple:
    if not f:
        return None, "not loaded"
    name = Path(f).name if isinstance(f, str) else Path(f.name).name
    return f, f"✅ {name}"


with gr.Blocks(
    title="Patent Claim Verifier",
    theme=gr.themes.Soft(),
    css=_CSS,
) as demo:

    # ── Shared state ─────────────────────────────────────────────────────────
    id_text_state    = gr.State(None)
    extra_text_state = gr.State(None)
    claim_text_state = gr.State(None)
    questions_state  = gr.State([])
    session_state    = gr.State({"phase": "idle"})
    qa_path_state    = gr.State(None)
    # raw file paths from UploadButton
    id_file_state    = gr.State(None)
    extra_file_state = gr.State(None)
    claim_file_state = gr.State(None)

    # ── Header ───────────────────────────────────────────────────────────────
    gr.Markdown("# 🔍 Patent Claim Verifier")

    # ── Upload bar (compact — UploadButton renders as a small button) ─────────
    with gr.Row(equal_height=True):

        # Left: three file pickers as button + filename label
        with gr.Column(scale=3):
            with gr.Row(elem_classes="upload-row"):
                id_btn   = gr.UploadButton("📄 Invention Disclosure *",
                                           file_types=[".docx"], size="sm", min_width=210)
                id_name  = gr.Textbox(show_label=False, placeholder="not loaded",
                                      interactive=False, container=False, scale=3)
            with gr.Row(elem_classes="upload-row"):
                extra_btn  = gr.UploadButton("📄 Additional Info (opt.)",
                                             file_types=[".docx"], size="sm", min_width=210)
                extra_name = gr.Textbox(show_label=False, placeholder="optional — not loaded",
                                        interactive=False, container=False, scale=3)
            with gr.Row(elem_classes="upload-row"):
                claim_btn  = gr.UploadButton("📄 Patent Claim *",
                                             file_types=[".docx"], size="sm", min_width=210)
                claim_name = gr.Textbox(show_label=False, placeholder="not loaded",
                                        interactive=False, container=False, scale=3)

        # Right: model selector + actions + status
        with gr.Column(scale=2, min_width=300):
            model_dd = gr.Dropdown(
                label="Ollama Model",
                choices=[],
                value=None,
                interactive=True,
                filterable=True,
                allow_custom_value=False,
                elem_id="model-selector",
            )
            with gr.Row():
                refresh_btn = gr.Button("🔄 Refresh", size="sm")
                load_btn    = gr.Button("📥 Load Documents", variant="primary", size="sm")
            load_status = gr.Textbox(
                label="Status",
                lines=2,
                max_lines=3,
                interactive=False,
                placeholder="Upload files then click Load…",
            )

    gr.Markdown("---")

    # ── Tabs ──────────────────────────────────────────────────────────────────
    with gr.Tabs():

        # Tab 1: Analyze
        with gr.TabItem("📊 Analyze"):
            gr.Markdown(
                "Compare the Invention Disclosure against the Patent Claims "
                "and generate a structured analysis report."
            )
            analyze_btn = gr.Button("▶ Run Analysis", variant="primary", size="lg")
            with gr.Row():
                with gr.Column(scale=2):
                    analysis_out = gr.Textbox(
                        label="Analysis (streaming)",
                        lines=25,
                        interactive=False,
                        show_copy_button=True,
                    )
                with gr.Column(scale=1):
                    analysis_dl = gr.File(
                        label="⬇ Download Analysis Report (.docx)",
                        interactive=False,
                    )

        # Tab 2: Verify Claim
        with gr.TabItem("✅ Verify Claim"):
            gr.Markdown(
                "Work through each question (extracted from comments in the Patent Claim doc) "
                "one by one. The LLM answers using the Invention Disclosure as context. "
                "Approve or retry each answer before moving on."
            )
            start_btn = gr.Button("🚀 Start Verification", variant="primary")

            chatbot = gr.Chatbot(
                label="Verification Chat",
                height=520,
                type="messages",
                show_copy_button=True,
                bubble_full_width=False,
            )
            with gr.Row():
                msg_box = gr.Textbox(
                    label="Your message",
                    placeholder="Type context, 'yes', or 'no'…",
                    lines=2,
                    scale=5,
                    submit_btn=False,
                )
                submit_btn = gr.Button("Submit ➤", variant="primary", scale=1)

            qa_dl = gr.File(
                label="⬇ Download Q&A Report (.docx)",
                interactive=False,
            )

    # ── Event wiring ──────────────────────────────────────────────────────────

    # File uploads → store path + show filename
    id_btn.upload(_on_upload,    inputs=[id_btn],    outputs=[id_file_state,    id_name])
    extra_btn.upload(_on_upload, inputs=[extra_btn], outputs=[extra_file_state, extra_name])
    claim_btn.upload(_on_upload, inputs=[claim_btn], outputs=[claim_file_state, claim_name])

    # Model refresh
    refresh_btn.click(refresh_models, outputs=model_dd)

    # Load documents (now uses stored file-path states, not gr.File components)
    load_btn.click(
        load_documents,
        inputs=[id_file_state, extra_file_state, claim_file_state],
        outputs=[id_text_state, extra_text_state, claim_text_state, questions_state, load_status],
    )

    # Analyze (streaming)
    analyze_btn.click(
        run_analysis_stream,
        inputs=[id_text_state, extra_text_state, claim_text_state, model_dd],
        outputs=[analysis_out, analysis_dl],
    )

    # Start verification
    start_btn.click(
        start_verification_full,
        inputs=[id_text_state, extra_text_state, questions_state, model_dd],
        outputs=[chatbot, session_state, qa_dl],
    ).then(
        lambda: None,
        outputs=[qa_path_state],
    )

    # Chat submit (button)
    submit_btn.click(
        _submit_wrap,
        inputs=[msg_box, chatbot, session_state, qa_path_state],
        outputs=[msg_box, chatbot, session_state, qa_path_state, qa_dl],
    )

    # Chat submit (Enter key)
    msg_box.submit(
        _submit_wrap,
        inputs=[msg_box, chatbot, session_state, qa_path_state],
        outputs=[msg_box, chatbot, session_state, qa_path_state, qa_dl],
    )

    # Populate models on startup
    demo.load(refresh_models, outputs=model_dd)


if __name__ == "__main__":
    demo.launch(share=False, show_error=True)
