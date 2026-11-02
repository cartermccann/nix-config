# Appended to upstream's root CMakeLists.txt by the Nix package. Upstream
# builds Pond.System without install rules and builds Pond.Screenshot.Native
# as a separate project; both are installed into one QML import directory.
set(POND_QML_INSTALL_DIR "lib/qt-6/qml" CACHE STRING "QML import directory, relative to the prefix")

install(TARGETS pond_system pond_systemplugin
    LIBRARY DESTINATION ${POND_QML_INSTALL_DIR}/Pond/System)
install(FILES "${CMAKE_BINARY_DIR}/Pond/System/qmldir"
    "${CMAKE_BINARY_DIR}/Pond/System/pond_system.qmltypes"
    DESTINATION ${POND_QML_INSTALL_DIR}/Pond/System)

add_subdirectory(screenshot/native)
