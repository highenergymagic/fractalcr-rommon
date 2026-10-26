/* Host-only libFuzzer target; no mounts, transfers, or lifecycle operations. */
#define main monitor_unit_main
#include "test-monitor.c"
#undef main
#include <stdint.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if(size>=8192) return 0;
    char text[8192];
    memcpy(text,data,size); text[size]=0;
    (void)reference_ok(text);
    for(unsigned i=0;i<NSET;i++) (void)valid_setting(i,text);
    return 0;
}
