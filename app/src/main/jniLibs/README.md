Place prebuilt native libraries here for APK packaging.

Required for HEV tunnel:
- `app/src/main/jniLibs/arm64-v8a/libhev-socks5-tunnel.so`
- `app/src/main/jniLibs/armeabi-v7a/libhev-socks5-tunnel.so`

Optional for emulator/testing:
- `app/src/main/jniLibs/x86_64/libhev-socks5-tunnel.so`
- `app/src/main/jniLibs/x86/libhev-socks5-tunnel.so`

Build-time packaging uses `jniLibs.srcDirs("src/main/jniLibs", "libs")`.
