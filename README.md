# ExtracTube

ExtracTube is an experimental pipeline for turning very long YouTube speech-to-text transcripts into structured, book-like documents.

The project is not a simple summarizer. Its main question is:

> How can a short-context local LLM generate a globally coherent long document when it can only see small parts of the source at a time?

ExtracTube approaches this as an intermediate-representation problem. Instead of asking an LLM to summarize raw transcript chunks directly, the system first restores local meaning, structures the restored segments, builds a topic graph, creates a book-level blueprint, and only then writes sections from selected evidence.

## Current Status

ExtracTube is currently in the notebook-first prototyping stage.

The previous frontend/backend direction has been intentionally removed from the active scope. A web application may be designed later, but only after the notebook pipeline proves the core document-generation workflow.

Current focus:

- Validate YouTube STT ingestion.
- Convert raw transcript chunks into decontextualized semantic segments.
- Build segment-topic-role metadata.
- Discover a topic graph from segments.
- Derive a book outline and global writing blueprint.
- Generate leaf-level evidence packs.
- Write sections independently and revise them for global consistency.

## Problem Definition

ExtracTube assumes the following constraints:

- Input: a very long YouTube transcript, potentially too large to fit in any practical local LLM context window.
- Output: a structured, readable, book-like document.
- Constraint: the LLM must operate with local context only.

The goal is not to make the LLM see the whole transcript. The goal is to design representations and processing stages that preserve enough meaning for later steps to produce a coherent document.

## Core Design Principles

### 1. The LLM Never Sees the Whole Source

Every LLM-based stage works on local context only. Global consistency is handled through structured intermediate data and later revision passes, not by feeding the entire transcript into a single prompt.

### 2. Writing Does Not Depend Directly on Raw STT

Raw STT chunks are noisy and context-dependent. Final writing should be based on decontextualized segments, not raw chunks.

Raw chunks are still useful as evidence, but they are not the primary writing unit.

### 3. The Internal Structure Is a DAG, Not a Tree

The final document is presented as a tree:

```text
Book
  Chapter
    Section
      Subsection
```

Internally, however, evidence is graph-shaped. A single segment may support multiple topics, and a single topic may draw from many segments.

```text
Segment S143
  -> Topic A
  -> Topic B
  -> Topic C
```

### 4. Intermediate Representations Matter More Than Generation

The quality of the final document depends mainly on the quality of the intermediate representations:

```text
1. decontextualized_segment
2. entity / coreference resolution
3. book_blueprint
4. leaf_evidence_pack
```

## Revised Pipeline

### Step 1. STT to Chunking

```text
raw transcript
  -> mechanical chunking with overlap
  -> semantic chunk adjustment
```

The goal is to minimize context loss and retain enough surrounding information for later coreference resolution.

### Step 2. Decontextualization

Each chunk is converted into standalone semantic units.

Example:

```text
Before:
"This matters because the bottleneck happens here."

After:
"When Transformer attention cannot reuse the KV cache efficiently,
GPU memory bandwidth becomes the bottleneck and inference speed decreases."
```

This step resolves pronouns, implicit subjects, vague references, and context-dependent statements.

Expected output:

```json
{
  "id": "S143",
  "text": "...",
  "entities": [],
  "claims": []
}
```

### Step 3. Segment Structuring

Each decontextualized segment receives topic, role, and confidence metadata.

```json
{
  "segment_id": "S143",
  "topics": ["KV Cache", "Attention Bottleneck"],
  "roles": ["definition", "motivation"],
  "confidence": {}
}
```

Segments can belong to multiple topics. This is intentional.

### Step 4. Topic Graph Construction

The system builds a topic graph from segment-topic relationships.

This stage includes:

- topic clustering
- bottom-up topic discovery
- parent-child topic relationship inference
- many-to-many segment-topic mapping

### Step 5. Outline Generation

The discovered topic graph is reorganized into a readable book outline.

The original video order is not assumed to be the best book order. The outline should follow an educational and logical progression.

### Step 6. Book Blueprint Generation

The book blueprint defines global constraints for the whole document.

```json
{
  "target_reader": "...",
  "writing_style": "...",
  "global_concepts": [],
  "terminology": {},
  "chapter_roles": {},
  "do_not_repeat": [],
  "progression": "easy to advanced"
}
```

Without this blueprint, independently written sections are likely to conflict in terminology, level of detail, and assumed prior knowledge.

### Step 7. Leaf Evidence Pack Generation

Each leaf section receives a selected evidence pack instead of raw transcript chunks.

```json
{
  "leaf_title": "...",
  "role_in_book": "...",
  "required_prior_concepts": [],
  "new_concepts": [],
  "evidence_segments": [],
  "resolved_entities": {},
  "reuse_constraints": {}
}
```

The evidence pack tells the writer what to use, what to avoid repeating, and how the section fits into the larger book.

### Step 8. Leaf-Level Writing

Each leaf section is written independently from:

```text
- leaf_evidence_pack
- relevant parts of book_blueprint
```

This stage should be parallelizable because each writing task remains local-context bounded.

### Step 9. Chapter Stitching

Leaf sections are assembled into chapters.

This stage adds transitions, removes local duplication, and improves chapter-level flow.

### Step 10. Global Consistency Pass

The final revision pass checks the whole manuscript for:

- terminology consistency
- repeated explanations
- difficulty progression
- logical contradictions
- style consistency

## Key Technical Ideas

### Coreference Resolution

Long transcripts contain many references such as "this", "that method", "the previous issue", or implied subjects. Simple RAG or chunk summarization is not enough. ExtracTube needs an explicit decontextualization stage.

### Multi-Topic Assignment

A segment should not be forced into a single outline node too early. Many segments support multiple topics, and this many-to-many relationship should be preserved until evidence selection.

### Role-Based Evidence Selection

The same segment may serve as a definition in one section, an example in another, and motivation somewhere else. Evidence selection must consider both topic and role.

### DAG-Based Evidence, Tree-Based Output

The output document is a tree. The internal evidence model is a graph. ExtracTube keeps these two structures separate.

## How ExtracTube Differs From Linear Summarization

Typical YouTube-to-document systems often follow this pattern:

```text
transcript -> chunks -> summaries -> final document
```

ExtracTube follows this pattern instead:

```text
STT
  -> meaning restoration
  -> segment structuring
  -> topic graph
  -> book blueprint
  -> evidence-based writing
  -> global consistency revision
```

The system is designed around recoverable meaning, structured evidence, and global writing constraints.

## Project Layout

```text
notebooks/
  stt_test.ipynb
  README.md
```

The notebook plan is documented in [`notebooks/README.md`](notebooks/README.md).

## Known Limitations

- Perfect logical reconstruction from STT is impossible.
- Output quality depends heavily on STT quality.
- Coreference resolution may fail.
- Hallucinations can accumulate across stages.
- Computation cost may be high.
- Global coherence depends on the quality of intermediate representations.

## Summary

ExtracTube is a middle-representation-centered system for generating long, structured documents with short-context LLMs.

Its core flow is:

```text
STT -> meaning restoration -> structuring -> evidence-based writing -> global consistency revision
```