#!/usr/bin/env bash
set -e

# Paths to the libraries
if [ "$#" -eq 0 ]; then
    KISSAT_DIR="kissat/build_debug"
    CADICAL_DIR="cadical/build_debug"
elif [ "$#" -eq 2 ]; then
    KISSAT_DIR="$1"
    CADICAL_DIR="$2"
else
    echo "Usage: $0 [<kissat_build_dir> <cadical_build_dir>]"
    exit 1
fi
LIBKISSAT="$KISSAT_DIR/libkissat.a"
LIBCADICAL="$CADICAL_DIR/libcadical.a"

# Function to rename symbols in a library
rename_symbols() {
    local lib_path="$1"
    local prefix="$2"
    local dir=$(dirname "$lib_path")
    local lib_name=$(basename "$lib_path")
    local backup_lib="$lib_path.bak"
    local syms_file="$dir/redefine_syms.txt"

    if [ ! -f "$lib_path" ]; then
        echo "Error: Library $lib_path not found."
        return 1
    fi

    echo "Processing $lib_path..."

    # Create backup if it doesn't exist
    if [ ! -f "$backup_lib" ]; then
        cp "$lib_path" "$backup_lib"
        echo "Created backup: $backup_lib"
    else
        # Restore from backup to ensure we have a clean state
        cp "$backup_lib" "$lib_path"
        echo "Restored from backup: $backup_lib"
    fi

    # Extract kitten.o from the archive 
    # (assuming it's named kitten.o inside)
    ar x "$lib_path" kitten.o
    if [ ! -f "kitten.o" ]; then
        echo "Error: kitten.o not found in $lib_path"
        return 1
    fi

    # Generate symbol redefinition list from kitten.o
    # Look for defined global text/data symbols (T, D, B, R, etc.)
    # We want to rename ALL symbols defined in kitten.o to avoid conflicts.
    # Exclude undefined (U).
    nm -g --defined-only kitten.o | awk -v pre="$prefix" '{print $3 " " pre $3}' > "$syms_file"
    
    rm kitten.o

    if [ ! -s "$syms_file" ]; then
        echo "No global symbols found in kitten.o. Already renamed?"
        return 0
    fi
    
    # Debug: show symbols being renamed
    echo "Renaming the following symbols in $lib_path:"
    head -n 5 "$syms_file"
    echo "... (and others)"

    echo "Renaming symbols..."
    # Apply redefinition to the whole archive. 
    # This updates definitions in kitten.o AND citations in other objects if any.
    objcopy --redefine-syms="$syms_file" "$lib_path"

    echo "Verifying changes..."
    # Check if original symbols still exist as defined globals
    # We grep for symbols in the file to see if they are still T/D/etc.
    # But since we renamed them, they should be gone or only U (if referenced but not defined - wait, we renamed definitions).
    # Actually, we can just check if the new prefix exists.
    
    if nm -g "$lib_path" | grep " T $prefix" > /dev/null; then
        echo "Success: Symbols renamed to '$prefix...'"
    else
         echo "Warning: Could not find renamed symbols in $lib_path"
    fi
    
    rm "$syms_file"
    echo "------------------------------------------------"
}

# Process libkissat.a
rename_symbols "$LIBKISSAT" "kissat_"

# Process libcadical.a
rename_symbols "$LIBCADICAL" "cadical_"

echo "All done."
