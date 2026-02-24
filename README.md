# Patent Claim Verifier

A local-LLM pipeline for comparing Invention Disclosures against Patent Claims and running interactive Q&A verification — all data stays on your machine.

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) (local LLM server)
- ~5 GB free RAM per model (no GPU required)

---

## 1. Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Verify it's running:
```bash
ollama --version
```

Ollama runs as a background service automatically after install. To start it manually if needed:
```bash
ollama serve
```

---

## 2. Pull a Model

> **Recommended for your hardware** (Intel i5-1135G7, 15 GB RAM, CPU-only):

| Model | Pull command | RAM used | Speed | Quality |
|---|---|---|---|---|
| `llama3.1:8b` ⭐ **Best overall** | `ollama pull llama3.1:8b` | ~5 GB | Medium | Excellent — 128K context, great at structured reasoning |
| `mistral:7b` | `ollama pull mistral:7b` | ~4.5 GB | Fast | Good — solid for document analysis |
| `deepseek-r1:7b` | `ollama pull deepseek-r1:7b` | ~4.7 GB | Medium | Very good reasoning, slightly slower |
| `qwen2.5:7b` | `ollama pull qwen2.5:7b` | ~4.7 GB | Medium | Strong at technical/legal text |
| `llama3.2:3b` | `ollama pull llama3.2:3b` | ~2 GB | Very fast | Lower quality — use if RAM is tight |

> ⚠ Avoid models larger than 13B — on a CPU-only machine they will be impractically slow (minutes per response).

**Quick start (recommended):**
```bash
ollama pull llama3.1:8b
```

---

## 3. Install Python Dependencies

```bash
cd auto-claim-check
pip install -r requirements.txt
```

---

## 4. Run the App

```bash
python app.py
```

Open **http://localhost:7860** in your browser.

---

## 5. Usage

### Upload & Load
1. Click **📄 Invention Disclosure \*** → select your ID `.docx`
2. Click **📄 Additional Info (opt.)** → select supplementary `.docx` (optional)
3. Click **📄 Patent Claim \*** → select your claims `.docx`
4. Select a model from the dropdown (click **🔄 Refresh** if the list is empty)
5. Click **📥 Load Documents** — the status bar confirms how many questions were found

> Questions are extracted from **Word comments** in the Patent Claim document. Each comment becomes one verification question.

### Analyze Tab
- Click **▶ Run Analysis** to stream a comparative analysis (Coverage, Gaps, Strengths, Weaknesses, Consistency)
- Download the report as `.docx` when generation completes

### Verify Claim Tab
1. Click **🚀 Start Verification** — the first question is shown in chat
2. Type any additional context that might help answer the question, then click **Submit**
3. The LLM streams an answer using the Invention Disclosure as context
4. Type **`yes`** to approve → a final composed answer is saved and the next question is shown
5. Type **`no`** to retry → the same question is shown again with the previous answer visible for reference
6. Once all questions are approved, download the **Q&A report** as `.docx`

---

## 6. Test Documents

Sample documents for testing are included:

```bash
python create_test_docs.py   # generates test_id.docx, test_additional.docx, test_claim.docx
```

The claim document contains 3 embedded Word comments as test questions.

---

## Output Files

All generated reports are saved to the `outputs/` folder with timestamps:

```
outputs/
  analysis_20240125_143022.docx   ← from Analyze tab
  qa_report_20240125_144501.docx  ← from Verify tab
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Dropdown shows "no models" | Run `ollama serve` in a terminal, then click 🔄 Refresh |
| `ollama` not found | Re-run the install script or add `/usr/local/bin` to your PATH |
| No questions found | Make sure your Patent Claim `.docx` has **Word comments** (Insert → Comment in Word/LibreOffice) |
| Slow responses | Switch to `llama3.2:3b` or `mistral:7b` for faster CPU inference |
| Out of memory | Close other applications; use a 3B model |
