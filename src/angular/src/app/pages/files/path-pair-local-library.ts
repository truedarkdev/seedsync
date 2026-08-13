export type LocalLibraryState = "up_to_date" | "scanning" | "stale" | "waiting_for_scan";

export interface LocalLibrarySummary {
    fileCount: number | null;
    size: number | null;
    state: LocalLibraryState;
}

export function localLibrarySummary(summary: any): LocalLibrarySummary {
    const rawFileCount = summary?.local_library_file_count;
    const rawSize = summary?.local_library_size;
    const fileCount = rawFileCount === null || rawFileCount === undefined ? null : Number(rawFileCount);
    const size = rawSize === null || rawSize === undefined ? null : Number(rawSize);
    const state = summary?.local_library_state;
    return {
        fileCount: fileCount !== null && isFinite(fileCount) && fileCount >= 0 ? fileCount : null,
        size: size !== null && isFinite(size) && size >= 0 ? size : null,
        state: state === "up_to_date" || state === "scanning" || state === "stale"
            ? state : "waiting_for_scan"
    };
}

export function formatLocalFileCount(count: number | null): string {
    if (count === null) { return "\u2014"; }
    if (count < 1000) { return String(count); }
    const abbreviated = count / 1000;
    const precision = abbreviated < 100 ? 1 : 0;
    return abbreviated.toFixed(precision).replace(/\.0$/, "") + "k";
}

export function localLibraryStateLabel(state: LocalLibraryState): string {
    switch (state) {
        case "up_to_date": return "Up to date";
        case "scanning": return "Scanning";
        case "stale": return "Local scan failed";
        default: return "Waiting for scan";
    }
}

export function localLibraryDetail(state: LocalLibraryState, hasInventory: boolean): string | null {
    if ((state === "scanning" || state === "stale") && hasInventory) {
        return "Showing last complete scan";
    }
    return null;
}
