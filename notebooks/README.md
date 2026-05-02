# ExtracTube Notebook Plan

This directory contains the notebook-first implementation plan for ExtracTube.

The goal of the notebook phase is to validate the full document-generation pipeline before designing any frontend or backend application structure.

Each notebook should produce concrete artifacts that can be saved, inspected, and reused by later notebooks. The intended direction is to move from raw YouTube STT to intermediate representations, then to evidence-based section writing and global revision.

## Naming Convention

Use numbered notebooks so the implementation order is clear:

```text
00_stt_ingestion.ipynb
01_transcript_chunking.ipynb
02_decontextualization.ipynb
03_segment_structuring.ipynb
04_topic_graph.ipynb
05_outline_generation.ipynb
06_book_blueprint.ipynb
07_leaf_evidence_packs.ipynb
08_leaf_writing.ipynb
09_chapter_stitching.ipynb
10_global_consistency.ipynb
```

The current `stt_test.ipynb` is an early STT test notebook. It should eventually be renamed or folded into `00_stt_ingestion.ipynb` after the ingestion flow stabilizes.

## Suggested Artifact Layout

During notebook prototyping, write intermediate outputs to a local ignored directory such as:

```text
artifacts/
  raw/
  chunks/
  segments/
  graphs/
  outlines/
  blueprints/
  evidence_packs/
  drafts/
  final/
```

Each notebook should read from the previous stage and write a new artifact. This makes the pipeline restartable and easier to debug.

## 00_stt_ingestion.ipynb

Purpose:

- Download or load YouTube audio.
- Send audio to the local STT server.
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

Implementation notes:

- Keep the existing Qwen3-ASR / vLLM server test logic here.
- Separate audio download, transcription request, and transcript cleanup into reusable functions.
- Store both the raw STT response and cleaned text.
- Preserve segment-level timestamps for later evidence tracing.

Success criteria:

- A single YouTube URL can be converted into a clean transcript.
- The raw response and cleaned transcript are both saved.
- The notebook can be rerun without manually copying output between cells.

## 01_transcript_chunking.ipynb

Purpose:

- Split the cleaned transcript into chunks suitable for local-context LLM calls.
- Use mechanical chunking first, then apply semantic adjustment.

Inputs:

```text
artifacts/raw/transcript.json
artifacts/raw/transcript.txt
```

Outputs:

```text
artifacts/chunks/mechanical_chunks.json
artifacts/chunks/semantic_chunks.json
```

Implementation notes:

- Start with token- or character-based chunking with overlap.
- Keep timestamp ranges if the transcript has timestamps.
- Add overlap to reduce boundary context loss.
- Add a semantic pass that adjusts boundaries around topic changes, pauses, or sentence breaks.

Success criteria:

- Chunks are small enough for the target local LLM context.
- Each chunk includes source offsets or timestamps.
- Boundary quality can be inspected manually.

## 02_decontextualization.ipynb

Purpose:

- Convert each chunk into standalone semantic segments.
- Resolve pronouns, implied subjects, vague references, and context-dependent claims.

Inputs:

```text
artifacts/chunks/semantic_chunks.json
```

Outputs:

```text
artifacts/segments/decontextualized_segments.json
```

Target schema:

```json
{
  "id": "S0001",
  "source_chunk_id": "C0001",
  "text": "...",
  "entities": [],
  "claims": [],
  "source_spans": [],
  "confidence": {}
}
```

Implementation notes:

- This is the most important notebook in the pipeline.
- Use local context only: current chunk plus limited neighboring context.
- Ask the LLM to produce atomic claims rather than loose summaries.
- Preserve links back to source chunks for traceability.
- Detect uncertain resolutions instead of hiding them.

Success criteria:

- Each segment can be understood without reading the original chunk.
- Entity references are explicit.
- Claims are structured enough to support topic assignment.

## 03_segment_structuring.ipynb

Purpose:

- Add metadata to decontextualized segments.
- Assign topics, roles, entities, and confidence scores.

Inputs:

```text
artifacts/segments/decontextualized_segments.json
```

Outputs:

```text
artifacts/segments/structured_segments.json
```

Target schema:

```json
{
  "segment_id": "S0001",
  "topics": [],
  "roles": [],
  "entities": [],
  "claims": [],
  "confidence": {}
}
```

Implementation notes:

- Allow multiple topics per segment.
- Use role labels such as `definition`, `motivation`, `example`, `procedure`, `warning`, `transition`, and `summary`.
- Keep role assignment independent from topic assignment.
- Track confidence separately for topics, roles, and entity resolution.

Success criteria:

- Segments are searchable by topic and role.
- Multi-topic assignment is preserved.
- Low-confidence assignments are visible for later review.

## 04_topic_graph.ipynb

Purpose:

- Build a topic graph from structured segments.
- Discover topic clusters and relationships bottom-up.

Inputs:

```text
artifacts/segments/structured_segments.json
```

Outputs:

```text
artifacts/graphs/topic_graph.json
artifacts/graphs/topic_clusters.json
```

Implementation notes:

- Cluster similar topic labels.
- Merge duplicate or near-duplicate topics.
- Infer broader, narrower, and related-topic relationships.
- Preserve segment-topic many-to-many edges.

Success criteria:

- The graph shows which segments support which topics.
- Redundant topic labels are reduced.
- Topic relationships can be inspected before outline generation.

## 05_outline_generation.ipynb

Purpose:

- Convert the topic graph into a reader-facing book outline.
- Reorder topics into an educational and logical flow.

Inputs:

```text
artifacts/graphs/topic_graph.json
artifacts/graphs/topic_clusters.json
```

Outputs:

```text
artifacts/outlines/book_outline.json
artifacts/outlines/book_outline.md
```

Implementation notes:

- Do not preserve video order by default.
- Decide chapter, section, and subsection depth.
- Separate prerequisite concepts from advanced concepts.
- Keep links from outline leaves back to topic graph nodes.

Success criteria:

- The outline reads like a book structure, not a transcript timeline.
- Each leaf has enough supporting evidence available.
- The outline can be reviewed and edited manually.

## 06_book_blueprint.ipynb

Purpose:

- Create global writing constraints for the whole book.

Inputs:

```text
artifacts/outlines/book_outline.json
artifacts/segments/structured_segments.json
artifacts/graphs/topic_graph.json
```

Outputs:

```text
artifacts/blueprints/book_blueprint.json
```

Target schema:

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

Implementation notes:

- Define audience, tone, technical depth, and terminology.
- Decide which concepts should be introduced once and reused later.
- Record chapter-level roles such as introduction, foundation, application, and synthesis.
- Use this blueprint as a constraint for all later writing prompts.

Success criteria:

- The blueprint can guide independent section writing.
- Terminology and progression rules are explicit.
- Repetition constraints are clear enough to apply later.

## 07_leaf_evidence_packs.ipynb

Purpose:

- Build a compact evidence pack for each outline leaf.

Inputs:

```text
artifacts/outlines/book_outline.json
artifacts/blueprints/book_blueprint.json
artifacts/segments/structured_segments.json
artifacts/graphs/topic_graph.json
```

Outputs:

```text
artifacts/evidence_packs/leaf_evidence_packs.json
```

Target schema:

```json
{
  "leaf_id": "L0001",
  "leaf_title": "...",
  "role_in_book": "...",
  "required_prior_concepts": [],
  "new_concepts": [],
  "evidence_segments": [],
  "resolved_entities": {},
  "reuse_constraints": {}
}
```

Implementation notes:

- Select evidence by topic and role.
- Prefer decontextualized segments over raw STT text.
- Include only the evidence needed for the section.
- Add constraints about what should not be repeated.

Success criteria:

- Each leaf can be written from its evidence pack without reading the full transcript.
- Evidence remains traceable to original source chunks.
- Packs are small enough for local-context writing.

## 08_leaf_writing.ipynb

Purpose:

- Generate section-level drafts independently from evidence packs.

Inputs:

```text
artifacts/evidence_packs/leaf_evidence_packs.json
artifacts/blueprints/book_blueprint.json
```

Outputs:

```text
artifacts/drafts/leaf_drafts.json
artifacts/drafts/leaf_drafts.md
```

Implementation notes:

- Write each leaf using only its evidence pack and relevant blueprint constraints.
- Avoid adding unsupported claims.
- Record which evidence segments were used.
- Keep each writing call independent and parallelizable.

Success criteria:

- Each leaf draft is readable as a standalone section.
- Claims are grounded in evidence segments.
- The writing style follows the book blueprint.

## 09_chapter_stitching.ipynb

Purpose:

- Combine leaf drafts into coherent chapters.

Inputs:

```text
artifacts/drafts/leaf_drafts.json
artifacts/outlines/book_outline.json
artifacts/blueprints/book_blueprint.json
```

Outputs:

```text
artifacts/drafts/chapter_drafts.json
artifacts/drafts/chapter_drafts.md
```

Implementation notes:

- Add transitions between sections.
- Remove obvious local duplication.
- Ensure chapter introductions and conclusions match chapter roles.
- Keep changes traceable to leaf drafts where possible.

Success criteria:

- Chapters read as continuous prose.
- Section transitions are natural.
- Local repetition is reduced.

## 10_global_consistency.ipynb

Purpose:

- Revise the full manuscript for global coherence.

Inputs:

```text
artifacts/drafts/chapter_drafts.json
artifacts/blueprints/book_blueprint.json
```

Outputs:

```text
artifacts/final/book.md
artifacts/final/revision_report.json
```

Implementation notes:

- Normalize terminology across chapters.
- Remove repeated explanations.
- Check difficulty progression.
- Detect contradictions between chapters.
- Produce a revision report that explains what changed.

Success criteria:

- The final manuscript follows the blueprint.
- Terminology is consistent.
- Repetition and contradictions are explicitly checked.

## Implementation Order

Recommended first milestone:

```text
00_stt_ingestion.ipynb
01_transcript_chunking.ipynb
02_decontextualization.ipynb
```

This milestone proves whether raw YouTube input can become reliable semantic segments.

Recommended second milestone:

```text
03_segment_structuring.ipynb
04_topic_graph.ipynb
05_outline_generation.ipynb
06_book_blueprint.ipynb
```

This milestone proves whether the system can move from local segments to a global book structure.

Recommended third milestone:

```text
07_leaf_evidence_packs.ipynb
08_leaf_writing.ipynb
09_chapter_stitching.ipynb
10_global_consistency.ipynb
```

This milestone proves whether the structured evidence can produce a readable long-form document.

## Practical Notes

- Keep notebooks exploratory, but keep schemas stable.
- Save every intermediate artifact.
- Prefer JSON for machine-readable outputs and Markdown for human review.
- Avoid coupling notebooks to a future web architecture.
- Do not optimize for UI until the pipeline quality is proven.
- Treat failed examples as valuable data; keep error cases and revision notes.
