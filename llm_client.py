from typing import Generator
import ollama


def get_available_models() -> list[str]:
    """Return names of locally available ollama models."""
    try:
        response = ollama.list()
        # Handle both new object API and legacy dict API
        if hasattr(response, "models"):
            return [m.model for m in response.models if m.model]
        if isinstance(response, dict):
            return [
                m.get("name", m.get("model", ""))
                for m in response.get("models", [])
                if m.get("name") or m.get("model")
            ]
    except Exception as exc:
        print(f"[llm_client] Could not list ollama models: {exc}")
    return []


def _chunk_content(chunk) -> str:
    """Extract text content from an ollama streaming chunk."""
    if hasattr(chunk, "message"):
        return chunk.message.content or ""
    return chunk.get("message", {}).get("content", "")


def stream_analysis(
    id_text: str,
    claim_text: str,
    extra_text: str,
    model: str,
) -> Generator[str, None, None]:
    """Stream a comparative analysis between the Invention Disclosure and Patent Claims."""
    extra_section = f"\n\n## Additional Information\n{extra_text}" if extra_text.strip() else ""

    prompt = f"""You are a senior patent expert. Carefully compare the Invention Disclosure with the Patent Claims below and produce a structured analysis report.

## Invention Disclosure
{id_text}{extra_section}

## Patent Claims
{claim_text}

---

Provide a detailed analysis under these headings:

### 1. Coverage Assessment
How well do the claims cover the invention described in the disclosure? Identify which aspects are covered and which are not.

### 2. Identified Gaps
List specific aspects of the invention that are NOT covered by any claim.

### 3. Strengths
What are the strongest elements of the current claims?

### 4. Weaknesses & Improvement Suggestions
Identify weak or overly broad/narrow claims and suggest concrete improvements.

### 5. Consistency Check
Note any inconsistencies, mismatches, or contradictions between the disclosure and the claims.

Be specific; reference exact claim language and disclosure sections where relevant."""

    stream = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for chunk in stream:
        content = _chunk_content(chunk)
        if content:
            yield content


def stream_answer(
    question: str,
    id_text: str,
    extra_text: str,
    user_context: str,
    model: str,
) -> Generator[str, None, None]:
    """Stream an answer to a patent claim question using the ID document as context."""
    extra_section = f"\n\nAdditional Information:\n{extra_text}" if extra_text.strip() else ""

    system_content = f"""You are a patent expert helping to verify patent claims against an Invention Disclosure.

Invention Disclosure Document:
---
{id_text}{extra_section}
---

Your task is to answer questions about the patent claims based solely on the invention disclosure above. Be precise, specific, and cite relevant parts of the disclosure where applicable."""

    user_content = f"Question to answer:\n{question}"
    if user_context.strip():
        user_content += f"\n\nAdditional context provided by reviewer:\n{user_context}"
    user_content += "\n\nPlease provide a thorough, well-structured answer."

    stream = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ],
        stream=True,
    )
    for chunk in stream:
        content = _chunk_content(chunk)
        if content:
            yield content
