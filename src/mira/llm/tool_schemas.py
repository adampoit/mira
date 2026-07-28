"""Structured-output tool schemas the model fills in via tool calling.

These ``submit_*`` schemas define terminal results for review, indexing,
feedback-learning, and verification passes. They are distinct from the
repository tools the model calls while reasoning.
"""

SUBMIT_REVIEW_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_review",
        "description": "Submit your code review findings including comments, key issues, and a summary.",
        "parameters": {
            "type": "object",
            "properties": {
                "comments": {
                    "type": "array",
                    "description": "List of review comments on specific lines of code.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative file path."},
                            "line": {
                                "type": "integer",
                                "description": "Line number in the target file.",
                            },
                            "end_line": {
                                "type": ["integer", "null"],
                                "description": "End line for multi-line comments, or null.",
                            },
                            "severity": {
                                "type": "string",
                                "enum": ["blocker", "warning", "suggestion", "nitpick"],
                            },
                            "category": {
                                "type": "string",
                                "enum": [
                                    "bug",
                                    "security",
                                    "performance",
                                    "error-handling",
                                    "race-condition",
                                    "resource-leak",
                                    "maintainability",
                                    "clarity",
                                    "configuration",
                                    "other",
                                ],
                            },
                            "title": {"type": "string", "description": "Short title (<80 chars)."},
                            "body": {
                                "type": "string",
                                "description": "Detailed explanation of the issue. Use single backticks for inline code references. Do NOT use triple-backtick code blocks.",
                            },
                            "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            "existing_code": {
                                "type": "string",
                                "description": "Verbatim copy of the code from the diff that this comment targets. Must be an exact substring.",
                            },
                            "suggestion": {
                                "type": ["string", "null"],
                                "description": "Optional replacement code to fix the issue. Raw code only — do NOT wrap in backticks or markdown fences.",
                            },
                            "agent_prompt": {
                                "type": ["string", "null"],
                                "description": "Concise imperative instruction for AI coding agents.",
                            },
                        },
                        "required": [
                            "path",
                            "line",
                            "severity",
                            "category",
                            "title",
                            "body",
                            "confidence",
                            "existing_code",
                        ],
                    },
                },
                "key_issues": {
                    "type": "array",
                    "description": "1-3 most critical findings a human reviewer MUST examine.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "issue": {"type": "string"},
                            "path": {"type": "string"},
                            "line": {"type": "integer"},
                        },
                        "required": ["issue", "path", "line"],
                    },
                },
                "summary": {
                    "type": "string",
                    "description": "Brief overall summary of the review.",
                },
                "metadata": {
                    "type": "object",
                    "properties": {
                        "reviewed_files": {"type": "integer"},
                        "skipped_reason": {"type": ["string", "null"]},
                    },
                },
            },
            "required": ["comments", "summary"],
        },
    },
}

SUBMIT_CRITIQUE_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_critique",
        "description": (
            "For each draft review comment, grade how well the evidence in "
            "the diff supports the claimed issue."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "verdicts": {
                    "type": "array",
                    "description": "One verdict per draft comment, in input order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "index": {
                                "type": "integer",
                                "description": "Zero-based index of the draft comment.",
                            },
                            "evidence": {
                                "type": "string",
                                "enum": ["proven", "plausible", "unsupported"],
                                "description": (
                                    "proven: the shown code demonstrates the issue and the "
                                    "reasoning is correct. "
                                    "plausible: the issue is consistent with the shown code "
                                    "but depends on behaviour or code not shown — this is a "
                                    "valid grade for real findings, not a failure. "
                                    "unsupported: the shown code contradicts the claim, the "
                                    "reasoning is wrong, or it's a style preference dressed "
                                    "up as an issue."
                                ),
                            },
                            "reason": {
                                "type": "string",
                                "description": "One short sentence explaining the verdict.",
                            },
                        },
                        "required": ["index", "evidence", "reason"],
                    },
                },
            },
            "required": ["verdicts"],
        },
    },
}


SUBMIT_VERIFY_FIXES_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_fix_verification",
        "description": "Submit whether each previously reported review issue is fixed.",
        "parameters": {
            "type": "object",
            "properties": {
                "results": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "The supplied review thread ID.",
                            },
                            "fixed": {
                                "type": "boolean",
                                "description": "Whether the reported issue is fixed.",
                            },
                        },
                        "required": ["id", "fixed"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["results"],
            "additionalProperties": False,
        },
    },
}


SUBMIT_FILE_SUMMARIES_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_file_summaries",
        "description": "Submit structured summaries for the requested source files.",
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "language": {"type": "string"},
                            "summary": {"type": "string"},
                            "symbols": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "kind": {"type": "string"},
                                        "signature": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["name", "kind", "signature", "description"],
                                    "additionalProperties": False,
                                },
                            },
                            "imports": {"type": "array", "items": {"type": "string"}},
                            "symbol_references": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "source": {"type": "string"},
                                        "calls": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "path": {"type": "string"},
                                                    "symbol": {"type": "string"},
                                                },
                                                "required": ["path", "symbol"],
                                                "additionalProperties": False,
                                            },
                                        },
                                    },
                                    "required": ["source", "calls"],
                                    "additionalProperties": False,
                                },
                            },
                            "external_refs": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "kind": {"type": "string"},
                                        "target": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["kind", "target", "description"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": [
                            "path",
                            "language",
                            "summary",
                            "symbols",
                            "imports",
                            "symbol_references",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["files"],
            "additionalProperties": False,
        },
    },
}


SUBMIT_DIRECTORY_SUMMARIES_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_directory_summaries",
        "description": "Submit summaries for the requested repository directories.",
        "parameters": {
            "type": "object",
            "properties": {
                "directories": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                        "required": ["path", "summary"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["directories"],
            "additionalProperties": False,
        },
    },
}


SUBMIT_DIRECTORY_SUMMARY_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_directory_summary",
        "description": "Submit a summary for the requested repository directory.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
    },
}


SUBMIT_FEEDBACK_RULES_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_feedback_rules",
        "description": "Submit recurring code-review rules learned from human feedback.",
        "parameters": {
            "type": "object",
            "properties": {
                "rules": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "rule": {"type": "string"},
                            "rationale": {"type": "string"},
                            "evidence_count": {"type": "integer", "minimum": 2},
                        },
                        "required": ["rule", "rationale", "evidence_count"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["rules"],
            "additionalProperties": False,
        },
    },
}


SUBMIT_THREAD_REPLY_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_thread_reply",
        "description": (
            "Reply to a human's comment on one of your previous PR review "
            "suggestions. Classify their intent and write a short reply."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": ["disagreement", "question", "agreement", "other"],
                    "description": (
                        "disagreement = human refutes the suggestion / says it doesn't apply. "
                        "question = human is asking for clarification. "
                        "agreement = human is acknowledging or thanking. "
                        "other = anything else (off-topic, unclear)."
                    ),
                },
                "reply": {
                    "type": "string",
                    "description": (
                        "Your reply, 1-2 short sentences, plain text, no markdown. "
                        'No emojis, no apologies, no "as an AI". For disagreement, '
                        "concede gracefully. For questions, answer directly."
                    ),
                },
            },
            "required": ["intent", "reply"],
        },
    },
}


SUBMIT_WALKTHROUGH_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_walkthrough",
        "description": "Submit a high-level walkthrough summary of the pull request.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Brief overall summary of the PR."},
                "confidence_score": {
                    "type": "object",
                    "properties": {
                        "score": {"type": "integer", "minimum": 1, "maximum": 5},
                        "label": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["score", "label", "reason"],
                },
                "change_groups": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "files": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "path": {"type": "string"},
                                        "change_type": {
                                            "type": "string",
                                            "enum": ["added", "modified", "deleted", "renamed"],
                                        },
                                        "description": {"type": "string"},
                                    },
                                    "required": ["path", "change_type", "description"],
                                },
                            },
                        },
                        "required": ["label", "files"],
                    },
                },
                "effort": {
                    "type": "object",
                    "properties": {
                        "level": {"type": "integer", "minimum": 1, "maximum": 5},
                        "label": {"type": "string"},
                        "minutes": {"type": "integer"},
                    },
                    "required": ["level", "label", "minutes"],
                },
                "sequence_diagram": {
                    "type": ["string", "null"],
                    "description": "Mermaid sequence diagram or null.",
                },
            },
            "required": ["summary", "change_groups"],
        },
    },
}
