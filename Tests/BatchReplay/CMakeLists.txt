cmake_minimum_required(VERSION 3.22)
project(cyrinx_batch_replay LANGUAGES C)
set(core "${CMAKE_CURRENT_SOURCE_DIR}/../../Sources/CCyrinx")
add_library(replay SHARED jni.c
    ${core}/cyrinx_batch.c ${core}/cyrinx_profiles.c ${core}/cyrinx_sha256.c
    ${core}/cyrinx_bulk.c ${core}/cyrinx_fft.c
    ${core}/kissfft/kiss_fft.c ${core}/kissfft/kiss_fftr.c)
target_compile_features(replay PRIVATE c_std_11)
target_compile_definitions(replay PRIVATE kiss_fft_scalar=double)
target_include_directories(replay PRIVATE ${core}/include ${core}/kissfft)
target_link_libraries(replay PRIVATE m)
