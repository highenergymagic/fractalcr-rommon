#!/bin/bash
# sbsign 0.9.4 embeds wall-clock signingTime. Freeze only the signer process;
# RSA signing and OpenSSL's kernel randomness sources remain unchanged.
set -euo pipefail
export LC_ALL=C TZ=UTC
epoch=${SOURCE_DATE_EPOCH:-1792454400}
[[ "$epoch" =~ ^[0-9]+$ ]] || { echo 'Invalid SOURCE_DATE_EPOCH' >&2; exit 2; }
# Linux checks the signingTime against the certificate's validity interval.
# A development key may have been created after the source epoch. Derive a
# stable signing instant from both immutable inputs instead of backdating it.
certificate=
arguments=("$@")
for ((i=0; i<${#arguments[@]}; i++)); do
    if [[ "${arguments[i]}" == --cert ]]; then certificate=${arguments[i+1]}; fi
done
test -n "$certificate"
start=$(openssl x509 -in "$certificate" -noout -startdate)
end=$(openssl x509 -in "$certificate" -noout -enddate)
not_before=$(date -u -d "${start#notBefore=}" +%s)
not_after=$(date -u -d "${end#notAfter=}" +%s)
if (( epoch < not_before )); then epoch=$not_before; fi
(( epoch < not_after )) || { echo 'Build epoch exceeds signing certificate validity' >&2; exit 1; }
stamp=$(date -u -d "@$epoch" '+%Y-%m-%d %H:%M:%S')
exec faketime -f "$stamp" sbsign "$@"
