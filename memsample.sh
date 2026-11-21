#!/bin/bash
# Every 30 s: memory used and available (MB), load, and the biggest process, to say what a restart followed.
while true; do
    top=$(ps -eo rss,args --sort=-rss | sed -n 2p | cut -c1-70)
    echo "$(date -u +%H:%M:%S) $(free -m | awk '/^Mem:/ {print "used=" $3 " avail=" $7}') load=$(cut -d' ' -f1 /proc/loadavg) top=$top"
    sleep 30
done
