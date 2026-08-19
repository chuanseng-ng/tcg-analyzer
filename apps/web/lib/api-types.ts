/**
 * Generated from the FastAPI OpenAPI schema. Do not edit by hand.
 *
 * Regenerate with: pnpm --filter @tcg/web gen:api-types
 * See docs/adr/0001-language-boundaries-in-the-monorepo.md.
 */

export interface paths {
    "/analyses": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Start an analysis
         * @description Starts an analysis and, if the caller has no live session, opens one. No login and no registration: V1 identifies a user by an anonymous session token only (spec §53), returned in an HTTP-only cookie that every later call to this analysis must carry. A cookie naming a session that has expired or never existed is not an error — a new session is opened. Nothing about the caller is recorded.
         */
        post: operations["start_analysis_analyses_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/analyses/{analysis_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Report the state of one analysis
         * @description Returns the analysis, provided the caller's session cookie is the session that started it. An identifier that names nothing, an analysis belonging to another session, a missing cookie and an expired one all answer 404 with the same body, so this endpoint cannot be used to discover which analyses exist.
         */
        get: operations["read_one_analysis_analyses__analysis_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/analyses/{analysis_id}/run": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Run an analysis
         * @description Hands the analysis to a background worker and returns immediately (spec §8, §65): image processing and inference take far longer than an HTTP request should. The response says `queued`, which is an acknowledgement rather than a state — poll `GET /analyses/{id}` to see where the analysis has got to.
         *
         *     Only an analysis whose images have arrived can be run; spec §18's pipeline begins with them. Running one twice is safe: the worker claims the analysis with a conditional update, so a second job finds nothing to do rather than repeating the first.
         */
        post: operations["run_one_analysis_analyses__analysis_id__run_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cards/search": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Find cards in the catalog
         * @description Searches the canonical catalog so a user can find the card in their hand. Every filter is optional and they are ANDed; an empty query browses the catalog. `text` matches a fragment of the printed name without regard to case, and works for Japanese. `card_number` matches as a prefix of the printed number's numerator, so `25`, `025` and `025/165` all find the card printed `025/165`. Results are ordered by `(set_code, card_number, variant, id)` — a total order, so paging neither drops nor duplicates a row. Nothing matching is an empty page, never a 404. No prices, and no images (ADR 0004).
         */
        get: operations["search_cards_cards_search_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/cards/{card_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Return the canonical detail for one card
         * @description Returns everything the catalog records about one card — spec §6's Card block, apart from identification confidence, which belongs to an analysis rather than to a catalog record. No card images: ADR 0004 imports none, so the only card images this product shows are the user's own uploads. No prices: `GET /cards/{id}/market` is M4.
         */
        get: operations["read_card_cards__card_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/catalog/version": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Report the card catalog version in use
         * @description Returns the card catalog version this deployment is serving — spec §57's `card_database_version`, one of the seven fields an analysis records so it can be re-derived. Reads the database, which is why it is a separate endpoint from `/health`.
         */
        get: operations["read_catalog_version_catalog_version_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
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
         * AnalysisResponse
         * @description One analysis, as the API reports it.
         *
         *     `apps/web` generates its types from this model (ADR 0001), so the field
         *     names are a public contract.
         *
         *     Deliberately small. `session_id` is absent because it is ours and internal —
         *     the client holds a token, not a row id — and every §57 reproducibility field
         *     is absent because nothing writes one yet; a column that is always NULL in a
         *     response is an invitation to render an empty value. #35 adds the states this
         *     can hold, #104 fills `card_id`.
         */
        AnalysisResponse: {
            /**
             * Card Id
             * @description The card the user confirmed, or null before they have. Unknown until confirmation (spec §20), which is a step in the pipeline rather than a precondition of starting one.
             */
            card_id: string | null;
            /**
             * Completed At
             * @description When it reached a terminal state, or null while it has not.
             */
            completed_at: string | null;
            /**
             * Created At
             * Format: date-time
             * @description When the analysis was started.
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             * @description This service's identifier for the analysis.
             */
            id: string;
            /**
             * Status
             * @description One of spec §65's nine states. `created` until an upload moves it. `queued` is a transport word `POST /analyses/{id}/run` answers with and is never held here.
             * @example created
             */
            status: string;
        };
        /**
         * AnalysisRunResponse
         * @description The acknowledgement `POST /analyses/{id}/run` answers with — spec §65.
         *
         *     §65 names both fields, and names the first `analysis_id` rather than `id`.
         *     Transcribed rather than tidied: this is the shape a client was told to
         *     expect, and `AnalysisResponse` is a different message about a different
         *     thing.
         */
        AnalysisRunResponse: {
            /**
             * Analysis Id
             * Format: uuid
             * @description The analysis that was queued.
             */
            analysis_id: string;
            /**
             * Status
             * @description Always `queued`. A transport word meaning 'accepted, not started' — it is not one of spec §65's nine states and no analysis ever holds it. Poll `GET /analyses/{id}` for the state the analysis is in.
             * @constant
             */
            status: "queued";
        };
        /**
         * CardExternalIdResponse
         * @description One external database's identifier for this card.
         *
         *     Included because spec §10's third table is the seam that keeps catalog
         *     sources replaceable, and a support question about a wrong record is
         *     unanswerable without it. Several entries may share a `provider`: the index
         *     behind them is deliberately not unique (#23).
         */
        CardExternalIdResponse: {
            /**
             * External Id
             * @description The identifier as that provider issued it, verbatim.
             * @example bs-4-unlimited-holo
             */
            external_id: string;
            /**
             * Provider
             * @description A lowercase slug naming the source — 'manual' or 'tcgdex' in V1.
             * @example manual
             */
            provider: string;
        };
        /**
         * CardResponse
         * @description The body of a successful `GET /cards/{id}` — spec §6's Card block.
         *
         *     `apps/web` generates its types from this model (ADR 0001), so the field
         *     names are a public contract.
         *
         *     Two absences are deliberate. `identification_confidence` belongs to an
         *     analysis, not to a catalog card, and putting it here would invite a client
         *     to read a catalog lookup as an identification. `image_front` / `image_back`
         *     are always NULL in V1 — see the module docstring and ADR 0004.
         *
         *     `metadata` is carried even though `CatalogVersionResponse` omitted its own.
         *     The two are different cases: a version's metadata records how a run went,
         *     where a card's records facts about the card that have no field yet — the set
         *     total a "4/102" is read against, for instance — and #29 names it as part of
         *     the canonical record. It generates as an untyped record, which is the honest
         *     shape for a free-form field.
         */
        CardResponse: {
            /**
             * Card Number
             * @description The number printed on the card, verbatim.
             * @example 4/102
             */
            card_number: string;
            /**
             * External Ids
             * @description Every external database identifier recorded for this card.
             */
            external_ids: components["schemas"]["CardExternalIdResponse"][];
            /**
             * Game
             * @description A lowercase slug. 'pokemon' in V1, and a field rather than a constant.
             * @example pokemon
             */
            game: string;
            /**
             * Id
             * Format: uuid
             * @description This catalog's identifier for the card. Never a provider's.
             */
            id: string;
            /**
             * Language
             * @description An ISO 639-1 code, read through the set. Japanese sets are distinct sets with their own numbering, not translations.
             * @example en
             */
            language: string;
            /**
             * Metadata
             * @description Whatever the source carried that has no field of its own.
             * @example {}
             */
            metadata: {
                [key: string]: unknown;
            };
            /**
             * Name
             * @description The card's printed name, in its own language.
             * @example Charizard
             */
            name: string;
            /**
             * Rarity
             * @description The printed rarity, where the source records one.
             * @example Rare Holo
             */
            rarity: string | null;
            set: components["schemas"]["CardSetResponse"];
            /**
             * Variant
             * @description The printing variant. Economically load-bearing: holo, reverse holo and 1st edition trade at very different prices.
             * @example unlimited-holo
             */
            variant: string | null;
        };
        /**
         * CardSearchResponse
         * @description The body of a successful `GET /cards/search`.
         *
         *     `apps/web` generates its types from this model (ADR 0001), so the field
         *     names are a public contract.
         *
         *     `total` counts every match rather than the page, so a UI can say "1-20 of
         *     137" and know when to stop. It is read in the same statement as the rows, so
         *     the two always describe one catalog.
         */
        CardSearchResponse: {
            /**
             * Cards
             * @description The matches in this window, in the catalog's total order.
             */
            cards: components["schemas"]["CardSummaryResponse"][];
            /**
             * Limit
             * @description The window size that produced `cards`.
             * @example 20
             */
            limit: number;
            /**
             * Offset
             * @description How many matches this window skipped.
             * @example 0
             */
            offset: number;
            /**
             * Total
             * @description How many cards matched in full, across every page.
             * @example 137
             */
            total: number;
        };
        /**
         * CardSetResponse
         * @description The set a card was printed in, nested rather than linked.
         *
         *     Every screen that shows a card shows its set — a card number without one
         *     identifies nothing — so a second request would be a round trip for data the
         *     first already had to join against.
         */
        CardSetResponse: {
            /**
             * Id
             * Format: uuid
             * @description This catalog's identifier for the set. Never a provider's.
             */
            id: string;
            /**
             * Metadata
             * @description Whatever the source carried that has no field of its own.
             * @example {
             *       "total_cards": 102
             *     }
             */
            metadata: {
                [key: string]: unknown;
            };
            /**
             * Name
             * @description The set's printed name, in its own language.
             * @example Base Set
             */
            name: string;
            /**
             * Release Date
             * @description The day the set went on sale, where it is known. A date, never a timestamp.
             * @example 1999-01-09
             */
            release_date: string | null;
            /**
             * Set Code
             * @description The publisher's set identifier, verbatim.
             * @example BS
             */
            set_code: string;
        };
        /**
         * CardSummaryResponse
         * @description One search result — enough to tell it apart from its neighbours.
         *
         *     Deliberately smaller than `CardResponse`. `variant` is the reason this is
         *     not merely a name and a number: holo, reverse holo and 1st edition are
         *     different cards economically, and a user who picks the wrong one is given
         *     the wrong valuation later. `external_ids` is absent because it would be a
         *     query per result for something nobody chooses between cards on, and
         *     `metadata` because it generates as an untyped record and belongs to the
         *     detail view. No thumbnail: ADR 0004 imports no card images.
         *
         *     `GET /cards/{id}` is where the rest lives; `id` is how a caller gets there.
         */
        CardSummaryResponse: {
            /**
             * Card Number
             * @description The number printed on the card, verbatim.
             * @example 4/102
             */
            card_number: string;
            /**
             * Game
             * @description A lowercase slug. 'pokemon' in V1, and a field rather than a constant.
             * @example pokemon
             */
            game: string;
            /**
             * Id
             * Format: uuid
             * @description This catalog's identifier for the card. Never a provider's.
             */
            id: string;
            /**
             * Language
             * @description An ISO 639-1 code, read through the set.
             * @example en
             */
            language: string;
            /**
             * Name
             * @description The card's printed name, in its own language.
             * @example Charizard
             */
            name: string;
            /**
             * Rarity
             * @description The printed rarity, where the source records one.
             * @example Rare Holo
             */
            rarity: string | null;
            set: components["schemas"]["CardSetResponse"];
            /**
             * Variant
             * @description The printing variant. Shown in results because it is economically load-bearing: holo, reverse holo and 1st edition trade at very different prices, so choosing between them is the point of a search.
             * @example unlimited-holo
             */
            variant: string | null;
        };
        /**
         * CatalogVersionResponse
         * @description The body of a successful `GET /catalog/version`.
         *
         *     `apps/web` generates its types from this model — see ADR 0001.
         *
         *     The record's surrogate key and its publication ordinal are deliberately
         *     absent: neither is usable by a client, and publishing the ordinal would
         *     invite one to sort on an internal key. So is `metadata`, which is free-form
         *     and would generate as an untyped record rather than a contract.
         */
        CatalogVersionResponse: {
            /**
             * Generated At
             * Format: date-time
             * @description When the source data was produced or retrieved.
             */
            generated_at: string;
            record_counts: components["schemas"]["RecordCounts"];
            /**
             * Source
             * @description A lowercase slug naming where the catalog came from.
             * @example manual
             */
            source: string;
            /**
             * Source License
             * @description The licence the source data arrived under, where one applies (ADR 0004).
             * @example MIT
             */
            source_license: string | null;
            /**
             * Source Revision
             * @description The upstream revision imported, where the source has one.
             * @example 8f2c1ab
             */
            source_revision: string | null;
            /**
             * Version
             * @description The explicit, ordered catalog identifier. Never a moving pointer (spec §31).
             * @example pokemon-catalog-seed-v0.0.0
             */
            version: string;
        };
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
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
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
        /**
         * RecordCounts
         * @description How much of the catalog the run that published this version wrote.
         */
        RecordCounts: {
            /**
             * Cards
             * @description Cards the run wrote.
             * @example 22
             */
            cards: number;
            /**
             * External Ids
             * @description Provider identifiers the run wrote.
             * @example 23
             */
            external_ids: number;
            /**
             * Sets
             * @description Sets the run wrote.
             * @example 4
             */
            sets: number;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
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
    start_analysis_analyses_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnalysisResponse"];
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
            /** @description The analysis store could not be reached. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    read_one_analysis_analyses__analysis_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description The identifier `POST /analyses` answered with. */
                analysis_id: string;
            };
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
                    "application/json": components["schemas"]["AnalysisResponse"];
                };
            };
            /** @description No analysis is recorded under that identifier — for this caller. Outside the spec §66 taxonomy, which has no code meaning 'not found'. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
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
            /** @description The analysis store could not be reached. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    run_one_analysis_analyses__analysis_id__run_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description The identifier `POST /analyses` answered with. */
                analysis_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnalysisRunResponse"];
                };
            };
            /** @description No analysis is recorded under that identifier — for this caller. The same body `GET /analyses/{id}` answers with, for the same reason. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The analysis is not in a state a run may start from. Outside the spec §66 taxonomy, which has no code meaning 'conflict'. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
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
            /** @description The analysis store or the job queue could not be reached. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    search_cards_cards_search_get: {
        parameters: {
            query?: {
                /** @description A fragment of the card's printed name. Case-insensitive. */
                text?: string | null;
                /** @description A lowercase slug — 'pokemon' in V1. */
                game?: string | null;
                /** @description An ISO 639-1 code — 'en' or 'ja' in V1. */
                language?: string | null;
                /** @description The publisher's set identifier, as printed. */
                set_code?: string | null;
                /** @description What is printed on the card. Matched as a prefix. */
                card_number?: string | null;
                /** @description A printing variant, e.g. 'reverse-holo'. */
                variant?: string | null;
                /** @description Window size. */
                limit?: number;
                /** @description How many matches to skip. */
                offset?: number;
            };
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
                    "application/json": components["schemas"]["CardSearchResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
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
            /** @description The card catalog could not be reached. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    read_card_cards__card_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description This catalog's identifier for the card, as returned by a search. */
                card_id: string;
            };
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
                    "application/json": components["schemas"]["CardResponse"];
                };
            };
            /** @description No card is recorded under that identifier. `card_not_identified` from the spec §66 taxonomy; `details.card_id` says which. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
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
            /** @description The card catalog could not be reached. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    read_catalog_version_catalog_version_get: {
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
                    "application/json": components["schemas"]["CatalogVersionResponse"];
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
            /** @description The catalog could not be reached, or no version has been registered. `details.reason` says which. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
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
