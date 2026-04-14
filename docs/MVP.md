# ExtracTube MVP Specification

## 1. Overview

ExtracTube is a system that transforms YouTube videos into **structured, readable study documents**.

The goal is not simple summarization, but converting video content into a format that can be:

- read like a document
- studied like notes
- organized like a book

In short:

> YouTube video → structured learning document

---

## 2. MVP Scope

This MVP must strictly limit scope.

### Input
- Single YouTube video URL

### Output
- Structured study document (text / markdown)

---

## 3. Core Pipeline

The system follows a linear pipeline:

```text
YouTube URL
 → Extract transcript
 → Split into chunks
 → Summarize each chunk
 → Reconstruct structured document
 → Return result
4. Included Features
Process a single YouTube URL
Extract transcript
Chunk transcript into manageable units
Summarize chunks using LLM
Generate structured document (sections, summaries, key points)
Render result in web UI
5. Excluded Features (Do NOT implement)
Multiple video processing
Vector database
RAG
Reranking
User authentication
Persistent database
Whisper fallback STT
OrchEdge integration
Search or Q&A
Any form of navigation linked to timestamps

This stage focuses only on document generation.

6. Product Goal

The system should allow users to:

understand the core content of a video without watching it fully
read the content as a structured document
quickly grasp key ideas and concepts
7. Success Criteria

The MVP is successful if:

User inputs a YouTube URL
The system extracts transcript
The transcript is processed into chunks
Each chunk is summarized
A structured document is generated
The document is readable and coherent
8. Technology Stack
Backend
Python
FastAPI
Pydantic
Frontend
Next.js
React
Tailwind CSS
TypeScript
YouTube Processing
yt-dlp
transcript extraction
LLM
Replaceable backend (local or API)
Used for summarization and structuring
9. Architecture
Frontend (Next.js)
        ↓
Backend API (FastAPI)
        ↓
Processing Pipeline

1. YouTube Loader
2. Transcript Loader
3. Chunk Builder
4. Summarizer
5. Document Formatter
10. Project Structure
extracTube/
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ api/
│  │  │  └─ routes_video.py
│  │  ├─ schemas/
│  │  │  ├─ request.py
│  │  │  └─ response.py
│  │  ├─ services/
│  │  │  ├─ youtube_service.py
│  │  │  ├─ transcript_service.py
│  │  │  ├─ chunk_service.py
│  │  │  ├─ summarize_service.py
│  │  │  └─ format_service.py
│  │  └─ core/
│  │     └─ config.py
│  └─ requirements.txt
│
├─ frontend/
│  ├─ app/
│  ├─ components/
│  │  ├─ UrlInput.tsx
│  │  ├─ SummaryViewer.tsx
│  │  └─ DocumentViewer.tsx
│  ├─ lib/
│  │  └─ api.ts
│  └─ package.json
│
└─ docs/
   └─ ExtracTube_MVP.md
11. Data Structures
Transcript Segment
{
  "start": 12.5,
  "end": 25.3,
  "text": "..."
}
Chunk
{
  "chunk_id": 0,
  "text": "...",
  "segments": [...]
}
Section Summary
{
  "section_title": "...",
  "summary": "...",
  "key_points": [
    "...",
    "..."
  ]
}
Final Output
{
  "video": {
    "title": "...",
    "video_id": "...",
    "url": "..."
  },
  "sections": [...],
  "document_markdown": "..."
}
12. Backend API
POST /api/video/process
Request
{
  "url": "https://www.youtube.com/watch?v=..."
}
Response
{
  "video": {...},
  "sections": [...],
  "document_markdown": "..."
}
13. Backend Components
youtube_service.py
Parse YouTube URL
Extract video ID
Fetch metadata
transcript_service.py
Load transcript
Convert into segment list
chunk_service.py
Split transcript into chunks
Maintain semantic coherence
summarize_service.py
Summarize each chunk
Generate:
title
summary
key points
format_service.py
Combine sections into a single document
Generate markdown output
14. Frontend Requirements
Main Page
Input field for YouTube URL
Submit button
Loading state
Error handling
Document Viewer
Display structured document
Render markdown output
Clearly separate sections
Summary Viewer
Show section titles
Show summaries
Show key points
15. LLM Prompt Guidelines

The summarization must:

convert spoken transcript into readable text
remove redundancy and filler words
preserve original meaning
avoid hallucination
organize information clearly

Output format must be structured JSON.

16. Implementation Order

Follow this sequence strictly:

Setup FastAPI server
Setup Next.js frontend
Implement YouTube URL parsing
Implement transcript extraction
Implement chunking logic
Implement summarization
Implement document formatting
Connect frontend to backend
17. Constraints

Do NOT:

over-engineer abstractions
introduce unused components
add database prematurely
implement multi-video support
implement RAG or retrieval

Focus only on:

Working end-to-end pipeline

18. Definition of Done

The MVP is complete when:

A YouTube URL can be submitted
Transcript is extracted
Content is chunked
Summaries are generated
A structured document is produced
The document is readable and coherent
19. Future Extensions (Out of Scope)
Multi-video aggregation
Channel-level document generation
Search and Q&A
Concept graph
Flashcards / learning tools
Vector database
RAG
OrchEdge integration
20. Summary

ExtracTube MVP is defined as:

A system that converts a single YouTube video into a structured, readable study document.

The priority is not complexity, but:

correctness
clarity
working pipeline

Build a simple system that works end-to-end before adding any advanced features.


---