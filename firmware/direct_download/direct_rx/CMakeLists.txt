cmake_minimum_required(VERSION 3.16)
list(APPEND EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# 【改这里】烧 rx0 写 csi_rx0, 烧 rx1 写 csi_rx1, 烧 rx2 写 csi_rx2.
project(csi_rx2)
