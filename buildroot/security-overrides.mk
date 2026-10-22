# The latest Buildroot 2025.02 release predates OpenSSL's September security
# release. Keep the source tree untouched and use its package recipe/patches.
# This file is included before package definitions; override pins the version.
override LIBOPENSSL_VERSION = 3.5.9
