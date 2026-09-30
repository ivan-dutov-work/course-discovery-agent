# Skill: course-article-style

Write prose for this repo's article (and related specs) in the same voice as the
existing draft of `specs/article/DRAFT.md`. The voice is architectural-level analysis,
not a tutorial — "1,000 km up" — where every paragraph serves a single point,
every claim is scoped, and every tradeoff is named.

## Voice principles

**1. Principle-first, not feature-first.**
Introduce a LangGraph mechanism by the concept it implements (encapsulation,
durability, idempotency), not by its API name. The API name follows as the
concrete illustration, not the heading. A reader should walk away with the
design rationale, not just the incantation.

**2. The repo is illustration, not the subject.**
The codebase exists to ground an abstract claim — it is not the claim itself.
Never enumerate what "this codebase does" unless the fact is architecturally
necessary (e.g. a cross-reference to an earlier section that established it).
A sentence that starts with "This codebase applies RetryPolicy to six nodes"
or "Two findings from wiring this up" has lost the framing. Instead, describe
the mechanism's shape in general terms and use at most one repo-specific
phrase per subsection to anchor it. The reader is here for LangGraph
patterns, not for a tour of this repository.

**3. Every claim is scoped.**
Use "in this repo" / "not tested here" / "what was and wasn't verified"
sparingly — only when the scope boundary is the point (e.g. §12's explicit
disclaimer). In the main sections, hedging about implementation choices
distracts from the architectural argument. If a section describes a mechanism
that this particular graph doesn't use, say "not every graph needs this" or
"the deciding factor is topology" — not "this codebase doesn't use it." The
repo is an example, not a test subject.

**4. Name the cost alongside the benefit.**
If a pattern saves something, say what it costs. If a mechanism gives you
durability, say what it doesn't persist. A section that only lists upsides
reads like marketing. The reader should trust that you've considered the other
side because you wrote it down.

**5. Distinguish related concepts taxonomically.**
When two framework features look similar but address different concerns, call
the distinction out explicitly. Use "Worth being precise about", "The key
distinction", "This is the direct cost of" as signposts. Example: checkpointer
vs. Store, Send vs. job queue, `interrupt_before` vs. `interrupt()`.

**6. The code snippet serves the argument, not the other way around.**
Every snippet should be short enough to read at a glance. It exists to ground
an abstract claim, not to document the codebase. If a snippet needs
explanation, it's too long. Prefer showing the interesting three lines over
the complete function. And the snippet is the *only* place the specific node
name matters — the prose around it should describe the class of thing, not
the instance.

**7. No filler transitions.**
Never open with "In this section, we will explore" or close with "In
conclusion, as we have seen." Start at the point. End at the point. The
structure is the outline; the prose doesn't need to re-announce it.

**8. Framework criticism is fair game with evidence.**
If LangGraph has a gap or a behavior that surprises, name it — and back it
with a source (open issue number, a docs quote, a code path). The article is
credible because it's honest, not because it's positive.

**9. Metalanguage is a structural tool, not noise.**
Phrases like "Worth naming", "Worth being precise about", "The architectural
response", "The practical decision rule" are not padding. They signal to the
reader that a distinction or tradeoff follows. Use them deliberately and
sparingly.

**10. One point per paragraph.**
If a paragraph needs a second point, it needs a second paragraph. Dense is
fine; dense and multi-topic is not. The reader should be able to skim the
first sentence of every paragraph and reconstruct the argument.

**11. Sibling sections balance.**
If section 5.1 is one page and 5.2 is one paragraph, either 5.2 needs more
substance or it should be folded into 5.1. Uneven depth signals uneven
thinking. Exception: a deliberately short section that exists only to name a
concept that's detailed elsewhere (cross-reference it).

**12. Stay on LangGraph mechanics; the rest is a pointer.**
The article's subject is LangGraph's mechanisms (reducers, `Send`, subgraphs,
interrupts, checkpointing, `Store`, retry/cache policy, fallback) and what each
one forces you to decide. Tooling and infrastructure topics that surround an
agent (tracing backends, sampling strategy, metrics stacks, queue products,
compliance regimes) are in scope only for the part that a LangGraph mechanism
causes. Test each paragraph: if it would read the same in an article about any
service, cut it or reduce it to one sentence and a pointer. What stays is the
consequence specific to the graph, such as one run splitting into several traces
at an interrupt, or replay duplicating work after a checkpoint.

Do not write vendor comparisons, option lists, decision tables or "how X works"
primers for generic infrastructure. A subsection should answer "what does this
LangGraph mechanism force on me", not "what are my options for this tool".

Respect the section budget in `specs/article/OUTLINE.md`. Before drafting, read
the outline entry and its word count. If the draft is running well past it, or
the section has grown subsections the outline doesn't have, stop and ask the
user whether the scope is meant to change. If it is, update the outline and the
framing in `CLAUDE.md` first, so the draft follows the scope rather than
redefining it.

## Tone markers

Use these when they fit — not as a checklist, but as a reminder of what the
article sounds like:

| Instead of | Write |
|---|---|
| "This allows us to..." | "This gives you..." |
| "It should be noted that..." | "Worth naming: ..." |
| "The advantage of X is..." | "X pays off directly in..." |
| "One potential drawback is..." | "The cost is visible in..." |
| "X and Y are different" | "Conflating the two is ..." |
| "We recommend..." | "The practical decision rule: ..." |

## When not to use this style

- In code comments (use the `minimal-comments` skill instead)
- In commit messages (one-liner, imperative mood)
- In `CLAUDE.md` (concise operational instructions)
- In code itself (follow language conventions)
