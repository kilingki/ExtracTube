# ExtracTube Notebook Plan

This directory contains the notebook-first MVP plan for ExtracTube.

ExtracTube is not a YouTube summarizer and not an STT cleanup tool. Its goal is to reconstruct a YouTube video into a book-like reading experience:

> Instead of directly consuming a YouTube video, the user should be able to read the material as a reconstructed book.

The final output should satisfy these conditions:

```text
1. It is not a chronological listing of the video.
2. It is not a simple summary.
3. It is not a cleaned-up STT transcript.
4. It can be understood by a reader starting from the beginning.
5. It has a book-like table of contents and narrative flow.
```

## MVP Pipeline

The current ExtracTube MVP should stay focused on five essential stages:

```text
01_STT
02_Segment Generation
03_Knowledge Map
04_Outline Generation
05_Book Writing
```

In one line:

```text
YouTube STT
  -> standalone segment generation
  -> knowledge map construction
  -> book outline generation
  -> book writing
```

Anything below this tends to become a summarizer or lecture-note generator. Anything much larger than this is likely to become over-engineered before the core book-generation workflow is proven.

The five stages are not equally difficult:

```text
01 STT                *
02 Segment Generation *****
03 Knowledge Map      ****
04 Outline Generation **
05 Book Writing       *****
```

For the MVP, the highest-risk stages are `02_segment_generation.ipynb` and `05_book_writing.ipynb`. In particular, the whole pipeline depends on whether a 30-60 minute YouTube lecture can become a set of independently understandable segments. If that fails, the knowledge map, outline, and final book will all inherit the same ambiguity.

## Naming Convention

Use numbered notebooks so the implementation order is clear:

```text
01_stt.ipynb
02_segment_generation.ipynb
03_knowledge_map.ipynb
04_outline_generation.ipynb
05_book_writing.ipynb
```

Existing exploratory notebooks such as `stt_test.ipynb`, `00_stt_ingestion.ipynb`, and `01_transcript_chunking.ipynb` can be kept during prototyping, but the stable MVP plan should converge on the five-stage structure above.

## Suggested Artifact Layout

Each notebook should write concrete artifacts that can be inspected and reused by later notebooks.

```text
artifacts/
  raw/
  segments/
  knowledge_maps/
  outlines/
  drafts/
  final/
```

The pipeline should be restartable from any major stage. Prefer JSON for machine-readable artifacts and Markdown for human review.

## 01_stt.ipynb

Purpose:

- Download or load YouTube audio.
- Send audio to the STT system.
- Normalize the STT response into a stable transcript format.
- Preserve timestamps when available.

Inputs:

```text
YouTube URL
STT server endpoint
STT model name
```

Outputs:

```text
artifacts/raw/transcript.json
artifacts/raw/transcript.txt
```

Success criteria:

- A single YouTube URL can be converted into a clean transcript.
- The raw STT response and normalized transcript are both saved.
- Timestamp or source-span information is preserved when available.

## 02_segment_generation.ipynb

Purpose:

- Convert the STT transcript into standalone, independently understandable segments.
- Restore missing context from spoken language.
- Resolve vague references, pronouns, implied subjects, and context-dependent phrases.
- Attach useful metadata for later mapping and writing.

This stage intentionally absorbs the older implementation details:

```text
chunking
+ decontextualization
+ segment structuring
```

Chunking may still be necessary to fit local LLM context limits, but it is not the conceptual output of the stage. The conceptual output is a set of standalone segments.

Example:

```text
Before:
"That is what you use when the previous method does not work."

After:
"Retrieval-Augmented Generation, or RAG, is a method for generating answers by searching external documents when a model cannot answer reliably from its internal knowledge alone."
```

Inputs:

```text
artifacts/raw/transcript.json
artifacts/raw/transcript.txt
```

Outputs:

```text
artifacts/segments/segments.json
```

Target schema:

```json
{
  "id": "S0001",
  "text": "...",
  "topics": [],
  "roles": [],
  "entities": [],
  "claims": [],
  "source_spans": [],
  "confidence": {}
}
```

Success criteria:

- Each segment can be understood without reading the original transcript around it.
- Important entities and claims are explicit.
- Segment-to-source traceability is preserved.
- Segments can support multiple topics when needed.

## 03_knowledge_map.ipynb

Purpose:

- Build a topic relationship map from the generated segments.
- Discover conceptual groupings and dependencies.
- Preserve many-to-many links between segments and topics.

This is the stage that prevents the output from simply following video order. Without it, the outline is likely to become a cleaned-up lecture timeline instead of a book structure.

Although the artifact is called a `Knowledge Map`, the implementation should stay closer to a topic organization layer than a full knowledge-graph system. The goal is not to research graph modeling, node taxonomy, or edge semantics. The goal is to organize topics well enough to produce a strong book table of contents.

Avoid making this stage more complex than the book-writing workflow needs. Terms such as graph, node, edge, prerequisite, and relationship are useful implementation vocabulary, but they should not pull the MVP toward a general-purpose knowledge graph.

Example:

```text
RAG
  - Vector DB
  - Embedding
  - Retrieval

Agent
  - Tool
  - MCP
  - Planning
```

Inputs:

```text
artifacts/segments/segments.json
```

Outputs:

```text
artifacts/knowledge_maps/knowledge_map.json
artifacts/knowledge_maps/knowledge_map.md
```

Target schema:

```json
{
  "topics": [],
  "relationships": [],
  "segment_topic_edges": []
}
```

Success criteria:

- The map shows which segments support which topics.
- Related, broader, narrower, and prerequisite relationships are visible.
- Video order is no longer the main organizing structure.
- The map remains simple enough to review manually and use directly for outline generation.

## 04_outline_generation.ipynb

Purpose:

- Convert the knowledge map into a reader-facing book outline.
- Reorder topics into an educational and logical flow.
- Decide parts, chapters, sections, and subsections.

Inputs:

```text
artifacts/knowledge_maps/knowledge_map.json
artifacts/segments/segments.json
```

Outputs:

```text
artifacts/outlines/book_outline.json
artifacts/outlines/book_outline.md
```

Example:

```text
Part 1. LLM Foundations
  Chapter 1. Transformers
  Chapter 2. Retrieval-Augmented Generation

Part 2. Agents
  Chapter 3. Tools
  Chapter 4. Planning
```

Success criteria:

- The outline reads like a book table of contents, not a transcript timeline.
- Earlier chapters introduce concepts needed by later chapters.
- Each outline node can be linked back to supporting topics and segments.
- The outline can be reviewed and edited manually.

## 05_book_writing.ipynb

Purpose:

- Write the final book-like manuscript from the outline, knowledge map, and segments.
- Produce readable prose with coherent flow across chapters.
- Keep claims grounded in source segments where possible.

This stage absorbs the older implementation details:

```text
book blueprint
+ evidence packs
+ leaf writing
+ chapter stitching
+ global consistency revision
```

These may still be implemented internally, but for the MVP they belong under one conceptual stage: book writing.

Inputs:

```text
artifacts/outlines/book_outline.json
artifacts/knowledge_maps/knowledge_map.json
artifacts/segments/segments.json
```

Outputs:

```text
artifacts/drafts/book_draft.md
artifacts/final/book.md
```

Success criteria:

- The manuscript can be read from the beginning without watching the video.
- The writing follows the outline rather than the video's chronological order.
- Concepts are introduced before they are used heavily.
- Terminology is consistent across chapters.
- Repetition is reduced.
- Unsupported claims are avoided.

## Why Knowledge Map Is Required

A four-stage pipeline such as:

```text
STT
  -> Segment
  -> Outline
  -> Writing
```

is missing the step that breaks the source away from video order.

If the system goes directly from segments to outline, it is likely to preserve the lecture sequence. That can produce a useful lecture note, but not necessarily a book.

The knowledge map creates the structure needed to reorganize the material into educational order:

```text
Segment
  -> Knowledge Map
  -> Outline
```

That is the point where ExtracTube becomes a book-generation system rather than a summarization system.

## Practical Notes

- Keep notebooks exploratory, but keep artifact schemas stable.
- Save every intermediate artifact.
- Prefer explicit source links over untraceable generated text.
- Treat chunking as an implementation detail, not a product stage.
- Treat `Knowledge Map` as an internal name for topic organization, not as a mandate to build a complex graph system.
- Validate `01_stt.ipynb` and `02_segment_generation.ipynb` strongly before investing in later stages.
- Do not optimize for UI until the notebook pipeline proves the book-generation workflow.
- Keep failed examples and revision notes; they are useful for improving later prompts and schemas.
