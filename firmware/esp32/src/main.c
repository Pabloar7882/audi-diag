#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
/* No hardware mapping has been approved; this shell does not connect to a vehicle. */
void app_main(void) {
    ESP_LOGI("fis", "Waiting for hardware mapping; no K-Line transport configured");
}
