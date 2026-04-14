# ExtracTube MVP Specification

---

## 1. Overview

ExtracTube transforms YouTube videos into **structured, readable study documents**.

The system converts video content into formats that can be:

- read like a document
- studied like notes
- organized like a book

> YouTube video -> structured learning document

---

## 2. Core Design Principle

ExtracTube is **not** an inference engine.

It is a:

> workflow and document transformation system

### Model Inference Strategy

All model inference is handled externally:

```text
ExtracTube Backend
   ↓
External APIs (LLM / STT)
```

ExtracTube only:

- prepares inputs
- calls APIs
- processes outputs

---

## 3. MVP Scope

**Input**

- Single YouTube video URL

**Output**

- Structured study document (Markdown)

---

## 4. Core Pipeline

```text
YouTube URL
  -> Extract transcript
  -> Split into chunks
  -> Call LLM API for summarization
  -> Reconstruct structured document
  -> Return result
```

---

## 5. Included Features

- Process a single YouTube URL
- Extract transcript
- Chunk transcript into logical units
- Call external LLM API for summarization
- Generate structured document with:
  - sections
  - summaries
  - key points
- Render result in web UI

---

## 6. Excluded Features (Do NOT implement)

- Multiple video processing
- Vector database
- RAG
- Reranking
- User authentication
- Persistent database
- Whisper STT fallback
- OrchEdge integration
- Search / Q&A
- Timestamp-based navigation
- Any internal model inference

---

## 7. Product Goal

The system should enable users to:

- understand video content without watching it fully
- read content in structured form
- quickly grasp key concepts

---

## 8. Success Criteria

The MVP is successful if:

- A YouTube URL can be submitted
- Transcript is extracted
- Transcript is chunked
- LLM summaries are generated via API
- A structured document is produced
- The document is readable and coherent

---

## 9. Technology Stack

**Backend**

- Python
- FastAPI
- Pydantic

**Frontend**

- Next.js
- React
- Tailwind CSS
- TypeScript

**YouTube Processing**

- yt-dlp
- transcript extraction

**Inference (External)**

- LLM API (required)
- STT API (optional)

---

## 10. Architecture

```text
Frontend (Next.js)
        ↓
Backend API (FastAPI)
        ↓
Processing Pipeline
  1. YouTube Loader
  2. Transcript Loader
  3. Chunk Builder
  4. LLM API Client
  5. Document Formatter
```

---

## 11. Project Structure

```text
extracTube/
├─ backend/
│  ├─ app/
│  │  ├─ api/
│  │  ├─ services/
│  │  │  ├─ youtube_service.py
│  │  │  ├─ transcript_service.py
│  │  │  ├─ chunk_service.py
│  │  │  ├─ document_service.py
│  │  │  └─ inference_clients/
│  │  │     ├─ llm_client.py
│  │  │     └─ stt_client.py
│  │  ├─ schemas/
│  │  └─ core/
│  └─ requirements.txt
├─ frontend/
└─ docs/
```

---

## 12. Data Structures

**Transcript Segment**

```json
{
  "start": 12.5,
  "end": 25.3,
  "text": "..."
}
```

**Chunk**

```json
{
  "chunk_id": 0,
  "text": "...",
  "segments": []
}
```

**Section Summary**

```json
{
  "section_title": "...",
  "summary": "...",
  "key_points": ["...", "..."]
}
```

**Final Output**

```json
{
  "video": {},
  "sections": [],
  "document_markdown": "..."
}
```

---

## 13. Backend API

`POST /api/video/process`

**Request**

```json
{
  "url": "https://www.youtube.com/watch?v=..."
}
```

**Response**

```json
{
  "video": {},
  "sections": [],
  "document_markdown": "..."
}
```

---

## 14. Backend Components

`youtube_service.py`

- Parse URL
- Extract metadata

`transcript_service.py`

- Load transcript
- Convert to segments

`chunk_service.py`

- Build chunks

`llm_client.py`

- Call external LLM API

`document_service.py`

- Build final document

---

## 15. Frontend Requirements

**Main Page**

- URL input
- Submit button
- Loading / error state

**Document Viewer**

- Render structured document
- Display sections clearly

---

## 16. LLM Requirements

LLM must:

- convert spoken text into readable format
- remove redundancy
- preserve meaning
- avoid hallucination
- output structured JSON

---

## 17. Implementation Order

1. Setup FastAPI
2. Setup Next.js
3. Implement YouTube parsing
4. Implement transcript extraction
5. Implement chunking
6. Implement LLM API client
7. Implement document generation
8. Connect frontend

---

## 18. Constraints

Do **not**:

- implement model inference locally
- add unnecessary abstraction
- introduce DB early
- implement RAG

Focus only on:

- End-to-end working pipeline

---

## 19. Definition of Done

- URL input works
- Transcript extracted
- Chunking works
- LLM API works
- Document generated
- UI renders output

---

## 20. Summary

ExtracTube MVP is:

> A system that converts a single YouTube video into a structured study document using external model APIs.

Priority:

- correctness
- clarity
- working pipeline

---