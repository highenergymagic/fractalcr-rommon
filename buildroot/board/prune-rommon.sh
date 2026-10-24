#!/bin/sh
set -eu
target=$1
# Remove only known unused outputs from the generated target tree, including
# leftovers when switching an incremental build from the default shell.
test -f "$target/init"
test -f "$target/usr/lib/libcrypto.so.3"
# Recovery networking uses native sockets and kernel APIs. Remove stale
# utilities from incremental builds that previously installed BusyBox.
find "$target/bin" "$target/sbin" "$target/usr/bin" "$target/usr/sbin" \
    -type l -lname '*busybox*' -delete
rm -f "$target/bin/busybox" "$target/bin/sh" "$target/bin/dash" \
    "$target/usr/lib"/libstdc++* "$target/usr/lib"/libgomp* \
    "$target/usr/lib"/libssl* "$target/lib"/libgcc_s* \
    "$target/usr/lib/ossl-modules/legacy.so"
