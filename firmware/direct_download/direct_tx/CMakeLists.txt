cmake_minimum_required(VERSION 3.16)
list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# 【改这里】烧 tx0 写 csi_tx0, 烧 tx1 写 csi_tx1, 烧 tx2 写 csi_tx2.
# 工程名只影响 build/ 目录的子文件夹名, 不影响固件逻辑.
project(csi_tx2)
