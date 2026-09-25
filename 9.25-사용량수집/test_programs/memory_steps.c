#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(void) {
    char *blocks[8] = {0};

    for (int i = 0; i < 8; i++) {
        blocks[i] = malloc(8 * 1024 * 1024);

        if (blocks[i] != NULL) {
            memset(blocks[i], 1, 8 * 1024 * 1024);
        }

        usleep(250000);
    }

    sleep(1);

    for (int i = 0; i < 8; i++) {
        free(blocks[i]);
    }

    return 0;
}
