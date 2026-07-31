/*
 * VibePaste bundle executable.
 *
 * The bundle executable must be a real Mach-O binary, not a #! script.
 *
 * With a script, two things go wrong, both silently:
 *
 *   1. The kernel runs the *interpreter*, so NSBundle.mainBundle() is
 *      Python.app. VibePaste.app's own Info.plist is then never read, which
 *      loses LSUIElement (Dock rocket) and NSMicrophoneUsageDescription
 *      (TCC kills the process if it asks for the microphone without it).
 *   2. venv/bin/python3 re-execs into the framework interpreter. Replacing
 *      the process image after LaunchServices registered it means the app
 *      never checks in, and its menu bar item is created 0 pixels high and
 *      never drawn.
 *
 * Embedding the interpreter avoids both: no exec happens, and the running
 * executable lives inside VibePaste.app, so that is the main bundle. TCC
 * permissions are then attributed to "VibePaste" rather than to "Python",
 * and survive Homebrew upgrading the interpreter.
 *
 * Py_BytesMain runs the script exactly as `python <script>` would.
 */

#include <Python.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <string.h>

/* Contents/Resources/main.py, resolved relative to this binary so the
   bundle can be moved or copied without rebuilding. */
static int resolve_script(char *out, size_t size) {
    char path[PATH_MAX];
    uint32_t length = sizeof(path);
    if (_NSGetExecutablePath(path, &length) != 0) {
        return -1;
    }
    char resolved[PATH_MAX];
    if (realpath(path, resolved) == NULL) {
        return -1;
    }
    /* .../Contents/MacOS/VibePaste -> .../Contents */
    char *contents = dirname(dirname(resolved));
    return snprintf(out, size, "%s/Resources/main.py", contents) > 0 ? 0 : -1;
}

int main(int argc, char *argv[]) {
    char script[PATH_MAX];
    if (resolve_script(script, sizeof(script)) != 0) {
        fprintf(stderr, "VibePaste: could not locate Resources/main.py\n");
        return 1;
    }

    char *args[3];
    args[0] = argv[0];
    args[1] = script;
    args[2] = NULL;
    return Py_BytesMain(2, args);
}
