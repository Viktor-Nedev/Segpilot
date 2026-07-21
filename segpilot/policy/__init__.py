"""SEGPILOT's three-layer compression policy.

    static   -> conversation-aware (kind, level) per segment  [layer 1]
    guard    -> must-keep retention check, demote on failure  [layer 2]
    adaptive -> regret-driven level tuning                    [layer 3]
"""
