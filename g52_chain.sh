#!/bin/bash
# Gate g52 (g51's commits with O609, O610 and O611 folded in; g51's lane logs went with the container at 09:45).
# Restart-safe: a lane whose driver shows it green on this HEAD is not run again. Two lanes at a time:
# ui alone; ist with pg-c; then mig with pg-a, and pg-b when pg-a is done. No browser lane beside mig.
T=/home/user/work/g52g; L=/home/user/work/gate_g52; P=g52
BASE=587ef255
export GATE_TREE=$T GATE_LOGS=$L
mkdir -p "$L"; cd "$L" || exit 2
ts() { date -u +%H:%M; }
HEAD=$(git -C "$T" rev-parse HEAD)
passed() {
    local d="$L/$1_driver.txt"
    [ -f "$d" ] && grep -q "^HEAD $HEAD dirty=0" "$d" || return 1
    case $1 in
        ui)  ! grep -qiE "failed|Traceback" "$d" && grep -q "names: clean" "$d" && grep -q "^register: 0 row" "$d" \
                 && grep -q "No invariant findings" "$d" && grep -q "No changes detected" "$d" ;;
        mig) grep -q "^mig finished" "$d" && ! grep -qE "exit=[1-9]" "$d" ;;
        *)   grep -qE "exit=0$" "$d" && grep -q "^OK" "$d" && ! grep -q "^FAILED" "$d" ;;
    esac
}
lane() {  # lane <name> [env...]: run it unless it passed on this HEAD; a run again keeps the last driver
    local name=$1; shift
    if passed "$name"; then echo "$(ts) $name passed already"; return 0; fi
    [ -f "$L/${name}_driver.txt" ] && mv -f "$L/${name}_driver.txt" "$L/${name}_driver.txt.before_$(date -u +%H%M)"
    echo "$(ts) start $name"
    env "$@" bash "$T/scripts/gate/lane.sh" $P "$name" > "$L/${name}_driver.txt" 2>&1 < /dev/null
    passed "$name" || { echo "$(ts) STOP: $name did not pass, read $L/${name}_driver.txt"; return 1; }
    echo "$(ts) $name passed"
}
lane ui || exit 1
lane ist & a=$!; lane pg-c & c=$!
wait $a; ra=$?; wait $c; rc=$?
[ $ra = 0 ] && [ $rc = 0 ] || exit 1
lane mig GATE_BASE=$BASE & m=$!
lane pg-a && lane pg-b; rp=$?
wait $m; rm_=$?
[ $rp = 0 ] && [ $rm_ = 0 ] || exit 1
echo "$(ts) chain done"
