#pragma once

namespace boat::ipc {

enum class UdsCommand {
  START = 0,
  PAUSE = 1,
  STEP = 2,
  RESET = 3,
  STOP = 4,
  INJECT_FAULT = 5,
  QUERY_STATE = 6
};

}  // namespace boat::ipc
