import {PathPair} from "./path-pair.service";

export type PathPairRouteMatchType = "none" | "id" | "slug" | "ambiguous";

export interface PathPairRouteMatch {
    type: PathPairRouteMatchType;
    pathPair: PathPair | null;
}

const normalizeSlug = (value: string): string => {
    return (value || "")
        .trim()
        .toLowerCase()
        .normalize("NFKD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "");
};

const normalizeRouteSegment = (value: string): string | null => {
    if (value == null) {
        return null;
    }

    try {
        return decodeURIComponent(value);
    } catch (error) {
        return value;
    }
};

export const getPathPairRouteSegment = (pathPair: PathPair, enabledPathPairs: PathPair[]): string => {
    const pathPairSlug = normalizeSlug(pathPair.name);
    if (pathPairSlug === "") {
        return pathPair.id;
    }

    const hasSlugCollision = enabledPathPairs.some(otherPathPair =>
        otherPathPair.id !== pathPair.id && normalizeSlug(otherPathPair.name) === pathPairSlug
    );
    const hasIdCollision = enabledPathPairs.some(otherPathPair =>
        otherPathPair.id !== pathPair.id && otherPathPair.id === pathPairSlug
    );

    return hasSlugCollision || hasIdCollision ? pathPair.id : pathPairSlug;
};

export const resolvePathPairRouteSegment = (
    encodedPathPairSegment: string,
    enabledPathPairs: PathPair[]
): PathPairRouteMatch => {
    const pathPairSegment = normalizeRouteSegment(encodedPathPairSegment);
    if (pathPairSegment == null) {
        return {type: "none", pathPair: null};
    }

    const exactIdMatch = enabledPathPairs.find(pathPair => pathPair.id === pathPairSegment);
    if (exactIdMatch != null) {
        return {type: "id", pathPair: exactIdMatch};
    }

    const pathPairSlug = normalizeSlug(pathPairSegment);
    if (pathPairSlug === "") {
        return {type: "none", pathPair: null};
    }

    const slugMatches = enabledPathPairs.filter(pathPair => normalizeSlug(pathPair.name) === pathPairSlug);
    if (slugMatches.length === 1) {
        return {type: "slug", pathPair: slugMatches[0]};
    }

    if (slugMatches.length > 1) {
        return {type: "ambiguous", pathPair: null};
    }

    return {type: "none", pathPair: null};
};
