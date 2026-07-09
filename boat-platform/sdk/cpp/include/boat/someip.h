#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* SOME/IP header fields (per AUTOSAR PRS SOME/IP protocol). */
typedef struct SomeipHeader {
  uint16_t service_id;
  uint16_t method_id;
  uint32_t length;       /* length of remaining payload after this field */
  uint16_t client_id;
  uint16_t session_id;
  uint8_t  protocol_version;  /* = 1 */
  uint8_t  interface_version;
  uint8_t  message_type;     /* REQUEST=0x00, RESPONSE=0x80, ... */
  uint8_t  return_code;
} SomeipHeader;

#define SOMEIP_PROTOCOL_VERSION 1
#define SOMEIP_MAGIC_COOKIE     0x0000

/* Message types */
#define SOMEIP_MSG_REQUEST              0x00
#define SOMEIP_MSG_REQUEST_NO_RETURN    0x01
#define SOMEIP_MSG_NOTIFICATION         0x02
#define SOMEIP_MSG_RESPONSE             0x80
#define SOMEIP_MSG_ERROR                0x81

/* SOME/IP-SD (Service Discovery) message types */
#define SOMEIP_SD_FIND_SERVICE          0x00
#define SOMEIP_SD_OFFER_SERVICE         0x01
#define SOMEIP_SD_STOP_OFFER_SERVICE    0x02
#define SOMEIP_SD_SUBSCRIBE             0x06
#define SOMEIP_SD_SUBSCRIBE_ACK         0x07

#ifdef __cplusplus
}
#endif
