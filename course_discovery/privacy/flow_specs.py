from __future__ import annotations

from course_discovery.privacy.flow import NodeFlow, SUBJECT, external, flow, store

LLM = external("llm:openrouter", accepts=frozenset({SUBJECT}))
SEARCH = external("search:tavily", accepts=frozenset({SUBJECT}))

OUTER: dict[str, NodeFlow] = {
    "gateway": flow(
        reads={"user_query", "manager_feedback", "search_filters", "routing_decision"},
        writes={"search_filters", "routing_decision", "manager_feedback", "error", "discard_reason"},
        sinks=(LLM,),
        redacts={"user_query", "manager_feedback"},
    ),
    "research_agent": flow(),
    "review_gate": flow(),
    "router": flow(
        reads={"manager_feedback", "iteration_count", "max_iterations"},
        writes={"routing_decision", "rewrite_instructions", "iteration_count", "discard_reason"},
        sinks=(LLM,),
        redacts={"manager_feedback"},
    ),
    "augment_dispatch": flow(),
    "publish_node": flow(
        reads={"user_id", "user_query", "digest", "valid_courses", "run_id"},
        writes={"publish_status"},
        sinks=(store("outbox"),),
        declassifies={"publish_status": "delivery state only"},
    ),
    "discard_node": flow(reads={"discard_reason", "run_id"}, writes={"publish_status"}),
    "user_memory_update": flow(
        reads={"user_id", "valid_courses", "user_query", "manager_feedback", "publish_status", "run_id"},
        sinks=(store("users"), store("recommendation_events")),
    ),
}

RESEARCH: dict[str, NodeFlow] = {
    "research_entry": flow(),
    "research_done": flow(),
    "user_memory_lookup": flow(reads={"user_id"}, writes={"user_memory"}),
    "course_cache_lookup": flow(
        reads={"search_filters", "user_memory", "metrics"},
        writes={"cache_candidates", "cache_hits", "metrics", "research_notes"},
        declassifies={
            "cache_candidates": "catalog rows; memory only filters them",
            "cache_hits": "counter",
            "metrics": "counters",
            "research_notes": "fixed message",
        },
    ),
    "research_planner": flow(
        reads={"search_filters", "cache_candidates", "research_iteration", "completed_queries"},
        writes={"research_plan", "error"},
    ),
    "tavily_search_worker": flow(
        reads={"active_search_query", "run_id"},
        writes={"tavily_results", "completed_queries", "tavily_calls", "research_notes"},
        sinks=(SEARCH,),
        declassifies={"tavily_calls": "counter"},
    ),
    "candidate_extractor": flow(
        reads={"tavily_results"},
        writes={"extracted_candidates"},
        declassifies={"extracted_candidates": "keeps catalog fields only, drops the echoed query"},
    ),
    "aggregate": flow(reads={"cache_candidates", "extracted_candidates"}, writes={"scraped_courses"}),
    "dedup": flow(reads={"scraped_courses"}, writes={"deduplicated_courses"}),
    "evidence_validator": flow(
        reads={
            "search_filters",
            "user_memory",
            "deduplicated_courses",
            "metrics",
            "completed_queries",
            "tavily_calls",
        },
        writes={"validation_results", "valid_courses", "rejected_courses", "uncertain_courses", "metrics"},
        declassifies={
            "validation_results": "verdicts about catalog rows; memory only filters them",
            "valid_courses": "catalog rows; memory only filters them",
            "rejected_courses": "catalog rows; memory only filters them",
            "uncertain_courses": "catalog rows; memory only filters them",
            "metrics": "counters",
        },
    ),
    "replanner": flow(
        reads={
            "search_filters",
            "validation_results",
            "research_plan",
            "completed_queries",
            "valid_courses",
            "research_iteration",
            "metrics",
        },
        writes={"research_plan", "research_iteration", "metrics", "tavily_results", "extracted_candidates", "error"},
        declassifies={"metrics": "counters", "tavily_results": "reset", "extracted_candidates": "reset"},
    ),
    "course_cache_upsert": flow(
        reads={"valid_courses", "uncertain_courses", "validation_results"},
        sinks=(store("courses", frozenset()), store("course_evidence", frozenset())),
    ),
    "synthesizer": flow(
        reads={
            "valid_courses",
            "rewrite_instructions",
            "validation_results",
            "research_notes",
            "metrics",
            "research_plan",
            "routing_decision",
            "run_id",
        },
        writes={"digest", "rewrite_instructions"},
        sinks=(LLM,),
    ),
    "edge:dispatch_search_queries": flow(reads={"research_plan"}, writes={"active_search_query"}),
}

EDGES = {"edge:dispatch_search_queries"}
FLOWS = {**OUTER, **RESEARCH}
