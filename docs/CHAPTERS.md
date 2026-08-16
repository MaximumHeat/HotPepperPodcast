# Automatic Chapter Export

HotPepperPodcast preserves chapter markers authored on script lines and exports them without inventing or rewriting episode structure.

## Project input

Add a `chapter` value to a script line:

```yaml
script:
  - speaker: host
    chapter: Opening
    text: Welcome to the episode.
  - speaker: host
    text: The next line has no chapter marker.
  - speaker: host
    chapter: Main topic
    text: Now we begin the main topic.
```

The local web editor exposes the same field as **Chapter marker**.

## Timing and selection rules

- Only enabled script lines are rendered and considered.
- Only non-empty authored `chapter` values are exported.
- A disabled line never creates a chapter.
- Chapter start times use the actual synthesized WAV segment start, not a character-count estimate.
- Speaker pauses and preceding line pauses therefore appear in later chapter timestamps.
- Chapters remain in authored script order.
- Each chapter receives an `endTime` equal to the next chapter's start, or the rendered episode duration for the final chapter.
- Timestamps are rounded to milliseconds for deterministic, readable output.

A render with markers produces `chapters.json` beside the master audio. A render without markers remains unchanged and does not create the file.

## File format

The file uses the dependency-free Podcasting 2.0 JSON Chapters shape:

```json
{
  "author": "MaximumHeat",
  "chapters": [
    {
      "endTime": 1.75,
      "startTime": 0.0,
      "title": "Opening",
      "toc": true
    }
  ],
  "title": "Hello HotPepperPodcast",
  "version": "1.2.0"
}
```

The output is written with stable sorted keys and a trailing newline. It is intended to be easy to inspect, archive, test, and convert to embedded ID3/MP4 chapter metadata in a future format-specific milestone.

## Packages and feeds

When package export is enabled, `chapters.json` is copied into the package root and recorded in:

- the render `manifest.json` as `chapters_file`;
- the package `manifest.json`;
- `package-summary.json`;
- the package file list;
- the RSS item as:

```xml
<podcast:chapters url="chapters.json" type="application/json+chapters" />
```

The package feed declares the Podcast Namespace. Because the package is intentionally self-contained and offline/local, its chapter URL is relative. A hosted feed must replace this with a public absolute HTTPS URL before directory submission.

## SDLC and safety boundary

This feature follows the project’s standing SDLC guidelines:

- authored text remains the source of truth;
- no LLM, cloud service, or external fetch is needed;
- the schema remains backward-compatible at version 1;
- generated files are deterministic and represented in manifests;
- stale chapter files are removed on rerender only when recorded as generated;
- render/package rollback restores prior outputs after failure;
- provider-independent tests verify timing and package behavior.

## References

- [Podcasting 2.0 JSON Chapters](https://podcastcasting2.org/docs/podcast-namespace/examples/chapters/jsonChapters)
- [Podcasting 2.0 chapters tag](https://podcasting2.org/docs/podcast-namespace/tags/chapters)
