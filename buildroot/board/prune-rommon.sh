#!/bin/sh
set -eu
target=$1
# Remove only known unused outputs from the generated target tree, including
# leftovers when switching an incremental build from the default shell.
test -f "$target/init"
test -f "$target/usr/lib/libcrypto.so.3"
# The monitor invokes /bin/busybox with a fixed applet name. Remove applet
# symlinks, including leftovers from a previously broader BusyBox config.
find "$target/bin" "$target/sbin" "$target/usr/bin" "$target/usr/sbin" \
    -type l -lname '*busybox*' -delete
rm -f "$target/bin/sh" "$target/bin/dash" \
    "$target/usr/lib"/libstdc++* "$target/usr/lib"/libgomp* \
    "$target/usr/lib"/libssl* "$target/lib"/libgcc_s* \
    "$target/usr/lib/ossl-modules/legacy.so"
