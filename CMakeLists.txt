cmake_minimum_required(VERSION 3.21)
project(polybind LANGUAGES NONE)

# Not part of the wheel: the entry point of the development workflow, locally and in CI.
#   cmake -S . -B build && ctest --test-dir build --output-on-failure      (ruff, then pytest)
# The tools they need are the `test` extra of pyproject.toml.
find_package(Python3 REQUIRED COMPONENTS Interpreter)

enable_testing()

add_test(NAME ruff
         COMMAND ${Python3_EXECUTABLE} -m ruff check .
         WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})

add_test(NAME pytest
         COMMAND ${Python3_EXECUTABLE} -m pytest -q
         WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR})
