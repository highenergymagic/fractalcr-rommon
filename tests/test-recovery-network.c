/* Socket integration driver. Run only against a loopback fixture. */
#include "../rommon/monitor.c"
int secure_boot(void) { return 1; }
int verified_image(const char *path) { (void)path; return -1; }
void boot_image(int fd, int ignore, const char *image) { (void)fd; (void)ignore; (void)image; }
int write_all(int fd, const void *data, size_t length) {
 const unsigned char *p = data;
 while (length) {
  ssize_t n = write(fd,p,length);
  if (n < 0 && errno == EINTR) continue;
  if (n <= 0) return -1;
  p += n; length -= n;
 }
 return 0;
}
int main(int argc,char **argv) {
 if (argc == 2 && !strcmp(argv[1],"ping")) return recovery_ping("127.0.0.1") != 0;
 if (argc != 3) return 2;
 return recovery_tftp("127.0.0.1",argv[1],argv[2]) != 0;
}
