#!/usr/bin/env python3
import sys
import os

# Ensure current directory is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def main():
    # Parse --topic and --profile out of sys.argv
    topic_name = None
    args_to_remove = []
    
    for idx, arg in enumerate(sys.argv):
        if arg in ("--topic", "--profile"):
            if idx + 1 < len(sys.argv):
                topic_name = sys.argv[idx + 1]
                args_to_remove.extend([idx, idx + 1])
            else:
                print(f"Error: {arg} requires a value.")
                sys.exit(1)
                
    # Remove these elements in reverse order to preserve indices
    for idx in sorted(args_to_remove, reverse=True):
        sys.argv.pop(idx)
        
    if topic_name:
        from db import resolve_db_path
        # Standardize topic database location via resolve_db_path helper
        os.environ["DATABASE_PATH"] = resolve_db_path(f"topic_{topic_name}.db")

    if len(sys.argv) < 2:
        print("Usage: psyche [setup | ingest | query | chat | build-graph | guide | checkin | goal | experiment | log-metric | review | rules | compact-memory | connect | mem | start-mcp | web | brainstorm | gaps] [options]")
        sys.exit(1)
        
    subcommand = sys.argv[1].lower()
    
    # Modify sys.argv to strip the subcommand name for sub-parsers
    sys.argv.pop(1)
    
    if subcommand == "setup":
        import setup_cmd
        setup_cmd.run_setup()
    elif subcommand == "ingest":
        import ingest
        ingest.main()
    elif subcommand == "query":
        import query
        query.main()
    elif subcommand == "chat":
        # Force chat mode by appending the flag
        sys.argv.append("--chat")
        import query
        query.main()
    elif subcommand == "build-graph":
        import build_graph
        build_graph.main()
    elif subcommand == "compact-memory":
        import synthesis
        db_path = os.getenv("DATABASE_PATH")
        synthesis.compile_daily_logs(db_path_arg=db_path)
    elif subcommand == "guide":
        import guidance
        guidance.main()
    elif subcommand == "checkin":
        import guidance
        guidance.checkin_main()
    elif subcommand == "goal":
        import guidance
        guidance.goal_main()
    elif subcommand == "experiment":
        import guidance
        guidance.experiment_main()
    elif subcommand == "log-metric":
        import guidance
        guidance.log_metric_main()
    elif subcommand == "review":
        import guidance
        guidance.review_main()
    elif subcommand == "rules":
        import guidance
        guidance.rules_main()
    elif subcommand == "mem":
        import mem_cli
        mem_cli.main()
    elif subcommand == "connect":
        import connect
        import argparse
        ap = argparse.ArgumentParser(prog="psyche connect")
        ap.add_argument("client", nargs="?",
                        choices=["claude-code", "codex", "gemini", "antigravity", "cursor"],
                        help="agent to wire; omit to auto-wire every detected agent")
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args()
        if a.client:
            lines = connect.connect(a.client, dry_run=a.dry_run)
        else:
            lines = connect.auto_connect(force=True, dry_run=a.dry_run)
            if not lines:
                lines = [f"no supported agents detected ({', '.join(connect._CLIENT_MARKERS)})"]
        for line in lines:
            print(line)
    elif subcommand == "start-mcp":
        try:
            import mcp_server
            mcp_server.main()
        except ImportError:
            print("Error: mcp-server subcommand is not fully implemented yet.")
            sys.exit(1)
    elif subcommand == "web":
        import web.server
        web.server.main()
    elif subcommand == "brainstorm":
        import brainstorm
        from llm_client import LLMClient
        drift, count, topics, seed = 0.5, 5, None, None
        args = sys.argv[1:]
        for i, a in enumerate(args):
            if a == "--drift" and i + 1 < len(args): drift = float(args[i + 1])
            elif a == "--count" and i + 1 < len(args): count = int(args[i + 1])
            elif a == "--topics" and i + 1 < len(args): topics = args[i + 1].split(",")
            elif a == "--seed" and i + 1 < len(args): seed = args[i + 1]
        try:
            out = brainstorm.generate_hypotheses(count=count, drift=drift, topics=topics, seed=seed, llm=LLMClient())
            for h in out:
                if h.get("needs_hypothesis"):
                    print(f"\n[{h['id']}] RAW COLLISION ({h['source_a']['topic']} x {h['source_b']['topic']}, "
                          f"drift {h['drift']}) - write a falsifiable hypothesis bridging:"
                          f"\n    A ({h['source_a']['topic']}): {h['source_a']['snippet'][:200]}"
                          f"\n    B ({h['source_b']['topic']}): {h['source_b']['snippet'][:200]}")
                else:
                    print(f"\n[{h['id']}] {h['hypothesis']}\n    kill-test: {h['kill_test']}"
                          f"\n    ({h['source_a']['topic']} x {h['source_b']['topic']}, drift {h['drift']})")
            if not out:
                print("No new pairs this run (all seen or band empty). Try a different --drift.")
        except Exception as e:
            print(f"brainstorm: {e}")
    elif subcommand == "gaps":
        import brainstorm
        top, topics = 10, None
        args = sys.argv[1:]
        for i, a in enumerate(args):
            if a == "--top" and i + 1 < len(args): top = int(args[i + 1])
            elif a == "--topics" and i + 1 < len(args): topics = args[i + 1].split(",")
        out = brainstorm.report_gaps(top=top, topics=topics)
        gaps = out.get("cluster_gaps", [])
        if not gaps:
            print(out.get("note", "No gaps found."))
        for g in gaps:
            a, b = g["cluster_a"], g["cluster_b"]
            print(f"GAP  {a['topic']}/{a['source']} <-x-> {b['topic']}/{b['source']}  (sim {g['similarity']:.2f})")
    else:
        print(f"Unknown command: {subcommand}")
        print("Available commands: setup, ingest, query, chat, build-graph, guide, checkin, goal, experiment, log-metric, review, rules, compact-memory, connect, mem, start-mcp, web, brainstorm, gaps")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stderr.write("\nOperation cancelled by user.\n")
        sys.exit(130)
