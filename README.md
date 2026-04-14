# ExtracTube

ExtracTube is a prototype system that converts YouTube videos into structured, readable study documents.

## Goal

Transform video content into learning-friendly formats such as:

- structured summaries
- section-based study notes
- book-like readable documents

ExtracTube focuses on **content transformation**, not model inference.

---

## MVP Scope

- Process a single YouTube URL
- Extract transcript
- Chunk transcript
- Summarize chunks via external LLM API
- Generate structured study document
- Render results in a web UI

---

## Architecture Principle

ExtracTube does NOT run models directly.

Instead:

```text
Frontend
   ↓
Backend (ExtracTube)
   ↓
External Inference APIs (LLM / STT)

- All model inference is handled outside this repository
- ExtracTube acts as a workflow + orchestration layer

## Project Structure

backend/
frontend/
docs/

## Docs

See docs/MVP.md for the full MVP specification.


---