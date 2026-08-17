/**
 * Generated from the FastAPI OpenAPI schema. Do not edit by hand.
 *
 * Regenerate with: pnpm --filter @tcg/web gen:api-types
 * See docs/adr/0001-language-boundaries-in-the-monorepo.md.
 */

export interface paths {
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Report service health
         * @description Returns the liveness of the API and the version of the running application. Consults no database, network or filesystem, so it is safe to use as a readiness probe.
         */
        get: operations["read_health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/readiness": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Report whether the API can serve traffic
         * @description Report dependency health.
         *
         *     Degrades to 503 with a body rather than raising: an orchestrator reading a
         *     500 from a readiness probe learns only that the probe is broken, whereas a
         *     503 with `checks` names the dependency that is down.
         *
         *     Every check is reported, not just the first failure: an operator fixing a
         *     deployment wants the whole list, and a probe that stops at the first problem
         *     turns one outage into two round trips.
         */
        get: operations["readiness_readiness_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * ErrorCode
         * @description Spec §66, verbatim. The closed set of things that can go wrong.
         * @enum {string}
         */
        ErrorCode: "invalid_image" | "image_quality_failure" | "card_not_identified" | "market_data_unavailable" | "analysis_failed" | "insufficient_information" | "provider_error" | "internal_error";
        /**
         * ErrorResponse
         * @description The one error body this API produces.
         *
         *     `apps/web` generates its types from this model (ADR 0001), so the field
         *     names are a public contract.
         * @example {
         *       "code": "market_data_unavailable",
         *       "details": {
         *         "card_id": "base1-4"
         *       },
         *       "message": "No usable price is available for this card."
         *     }
         */
        ErrorResponse: {
            /** @description Machine-readable classification from the spec §66 taxonomy. */
            code: components["schemas"]["ErrorCode"];
            /**
             * Details
             * @description Optional structured context, e.g. which field was rejected.
             */
            details?: {
                [key: string]: unknown;
            } | null;
            /**
             * Message
             * @description Human-readable summary. Safe to show a user; never contains internal detail.
             */
            message: string;
        };
        /**
         * HealthResponse
         * @description The body of a successful ``GET /health``.
         *
         *     A typed model rather than a bare dict because the OpenAPI schema is the sole
         *     source of frontend types — see ADR 0001.
         */
        HealthResponse: {
            /**
             * Application Version
             * @description Version of the running application, recorded against every analysis for reproducibility (spec §57).
             * @example 0.0.0
             */
            application_version: string;
            /**
             * Status
             * @description Liveness of the service itself. Dependencies are not consulted.
             * @constant
             */
            status: "ok";
        };
        /**
         * ReadinessChecks
         * @description Per-dependency outcome. Further dependencies join this model as they land.
         */
        ReadinessChecks: {
            /**
             * Database
             * @description Whether the API could execute a trivial statement against PostgreSQL.
             * @enum {string}
             */
            database: "ok" | "unavailable";
            /**
             * Storage
             * @description Whether the API could reach the object store.
             * @enum {string}
             */
            storage: "ok" | "unavailable";
        };
        /**
         * ReadinessResponse
         * @description apps/web generates its types from this schema — see ADR 0001.
         */
        ReadinessResponse: {
            checks: components["schemas"]["ReadinessChecks"];
            /**
             * Status
             * @description `degraded` whenever any check failed; the response is then HTTP 503.
             * @enum {string}
             */
            status: "ok" | "degraded";
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    read_health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
            /** @description The request failed. `code` classifies it; see spec §66. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    readiness_readiness_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Every dependency answered. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadinessResponse"];
                };
            };
            /** @description The request failed. `code` classifies it; see spec §66. */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description At least one dependency did not answer. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadinessResponse"];
                };
            };
        };
    };
}
