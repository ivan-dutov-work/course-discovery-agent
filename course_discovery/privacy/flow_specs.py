from __future__ import annotations

from course_discovery.privacy.flow import NodeFlow, SUBJECT, external, flow, store

LLM = external("llm:openrouter", accepts=frozenset({SUBJECT}))
SEARCH = external("search:tavily", accepts=frozenset({SUBJECT}))
EMBED = external("embeddings:openrouter", accepts=frozenset({SUBJECT}))

OUTER: dict[str, NodeFlow] = {
    "parse_user_request": flow(
        reads={"user_query", "feedback_history", "search_filters", "routing_decision", "user_id"},
        writes={"search_filters", "routing_decision", "discard_reason"},
        sinks=(LLM,),
        redacts={"user_query", "feedback_history"},
    ),
    "course_research": flow(),
    "curate_user_memory": flow(),
    "await_human_review": flow(),
    "interpret_review_feedback": flow(
        reads={"manager_feedback", "feedback_history"},
        writes={
            "routing_decision",
            "rewrite_instructions",
            "discard_reason",
            "feedback_history",
            "manager_feedback",
        },
        sinks=(LLM,),
        redacts={"manager_feedback"},
    ),
    "send_approved_courses": flow(
        reads={"user_id", "user_query", "digest", "valid_courses"},
        writes={"publish_status"},
        sinks=(store("outbox"),),
        declassifies={"publish_status": "delivery state only"},
    ),
    "discard_run": flow(reads={"discard_reason"}),
    "record_review_outcome": flow(
        reads={
            "user_id",
            "valid_courses",
            "user_query",
            "feedback_history",
            "publish_status",
        },
        sinks=(store("users"), store("recommendation_events")),
    ),
}

RESEARCH: dict[str, NodeFlow] = {
    "load_user_profile": flow(reads={"user_id"}, writes={"user_memory"}),
    "find_known_courses": flow(
        reads={"search_filters", "user_memory", "metrics", "user_id"},
        writes={"cache_candidates", "metrics", "research_notes"},
        sinks=(EMBED,),
        redacts={"user_memory"},
        declassifies={
            "cache_candidates": "catalog rows; memory only filters them",
            "metrics": "counters",
            "research_notes": "fixed message",
        },
    ),
    "plan_web_search": flow(
        reads={"search_filters", "cache_candidates", "research_iteration", "completed_queries"},
        writes={"research_plan", "error"},
    ),
    "search_web_for_courses": flow(
        reads={"active_search_query"},
        writes={"tavily_results", "completed_queries", "research_notes"},
        sinks=(SEARCH,),
    ),
    "extract_courses_from_results": flow(
        reads={"tavily_results"},
        writes={"extracted_candidates"},
        declassifies={"extracted_candidates": "keeps catalog fields only, drops the echoed query"},
    ),
    "merge_known_and_found_courses": flow(reads={"cache_candidates", "extracted_candidates"}, writes={"scraped_courses"}),
    "remove_duplicate_courses": flow(reads={"scraped_courses"}, writes={"deduplicated_courses"}),
    "verify_course_claims": flow(
        reads={
            "search_filters",
            "user_memory",
            "deduplicated_courses",
            "metrics",
            "completed_queries",
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
    "plan_gap_search": flow(
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
    "save_verified_courses": flow(
        reads={"valid_courses", "uncertain_courses", "validation_results"},
        sinks=(store("courses", frozenset()), store("course_evidence", frozenset()), EMBED),
    ),
    "rank_and_summarize_courses": flow(
        reads={
            "valid_courses",
            "rewrite_instructions",
            "validation_results",
            "research_notes",
            "metrics",
            "research_plan",
            "routing_decision",
            "user_memory",
            "search_filters",
        },
        writes={"digest", "rewrite_instructions"},
        sinks=(LLM,),
        redacts={"user_memory"},
    ),
    "edge:dispatch_search_queries": flow(reads={"research_plan"}, writes={"active_search_query"}),
}

CURATOR: dict[str, NodeFlow] = {
    "load_context": flow(
        reads={"user_id", "feedback_history"},
        writes={"memory_update"},
        redacts={"feedback_history"},
        declassifies={"memory_update": "status string only"},
    ),
    "curator_model": flow(
        reads={"feedback_history"},
        sinks=(LLM,),
    ),
    "run_tools": flow(reads={"user_id", "valid_courses", "publish_status"}),
    "commit": flow(
        reads={"user_id", "feedback_history"},
        writes={"memory_update"},
        sinks=(store("user_preferences"), store("memory_updates"), EMBED),
        declassifies={"memory_update": "status string only"},
    ),
}

EDGES = {"edge:dispatch_search_queries"}
FLOWS = {**OUTER, **RESEARCH, **CURATOR}
