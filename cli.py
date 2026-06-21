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
        print("Usage: psyche [setup | ingest | query | chat | build-graph | guide | checkin | goal | experiment | log-metric | review | rules | compact-memory | connect | mem | start-mcp | web] [options]")
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
        ap.add_argument("client", choices=["claude-code", "codex", "gemini", "antigravity"])
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args()
        for line in connect.connect(a.client, dry_run=a.dry_run):
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
    else:
        print(f"Unknown command: {subcommand}")
        print("Available commands: setup, ingest, query, chat, build-graph, guide, checkin, goal, experiment, log-metric, review, rules, compact-memory, connect, mem, start-mcp, web")
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.stderr.write("\nOperation cancelled by user.\n")
        sys.exit(130)
