cmake_minimum_required(VERSION 3.21)
project(polybind LANGUAGES NONE)

# Everything is built and run through CMake: `ctest` runs lint and the tests.
find_package(Python3 REQUIRED COMPONENTS Interpreter)

enable_testing()

add_test(NAME ruff
         COMMAND ${Python3_EXECUTABLE} -m ruff check .
         WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})

add_test(NAME pytest
         COMMAND ${Python3_EXECUTABLE} -m pytest -q
         WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
