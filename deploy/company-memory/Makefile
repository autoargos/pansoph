# Company Memory local POC shortcuts. Run from the repo root:
#   make -C deploy/company-memory demo
# Every target wraps `uv run company-memory <command>` (see docs/company-memory/DEMO.md).

CM := cd ../.. && uv run company-memory

.PHONY: demo init up seed sync verify audit down clean mcp-config

demo:        ; $(CM) demo
init:        ; $(CM) init
up:          ; $(CM) up
seed:        ; $(CM) seed
sync:        ; $(CM) sync
verify:      ; $(CM) verify
audit:       ; $(CM) audit
mcp-config:  ; $(CM) mcp-config
down:        ; $(CM) down
clean:       ; $(CM) down --volumes
