# Gemini Tool Usage Strategy

When modifying files, avoid using the `replace` tool due to its brittleness with `old_string` matching.

Instead, follow this process:
1. Read the entire file content using `read_file`.
2. Apply the necessary changes to the content in memory.
3. Write the entire modified content back to the file using `write_file`.

This is equivalent to "re-writing the line and then deleting the old line" but on a whole-file basis, which is more robust.
