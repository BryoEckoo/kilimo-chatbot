#!/bin/sh

echo -E $3 | /usr/bin/curl -X POST http://endpoints:4195/ai_results/$1/$2 --data @-