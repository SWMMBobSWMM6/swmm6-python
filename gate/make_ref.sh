#!/bin/bash
# Produce the SWMM6 reference outputs (gate/ref_outputs/<id>/) for every model in gate/models_all.tsv,
# with the reference engine ref/runswmm6 (single-threaded). Model paths may be relative to SWMM_CORPUS.
cd "$(dirname "$0")/.."
CORPUS=${SWMM_CORPUS:-$(pwd)/../1729-SWMM5-Models-2030}
while IFS=$'\t' read -r id path; do
  [[ "$path" = /* ]] || path="$CORPUS/$path"
  printf '%s\t%s\n' "$id" "$path"
done < gate/models_all.tsv | \
  xargs -P ${JOBS:-2} -d '\n' -I{} bash -c 'id=$(echo "{}" | cut -f1); p=$(echo "{}" | cut -f2-); GATE_TIMEOUT=${GATE_TIMEOUT:-600} gate/run_one.sh "'"$PWD"'/ref/runswmm6" "$p" "'"$PWD"'/gate/ref_outputs/$id"'
