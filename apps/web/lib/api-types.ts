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
    "/analyses/{analysis_id}/confirm-card": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Confirm which card the analysis is of
         * @description Records the card the user has confirmed they are holding (spec §20) and moves the analysis on from `awaiting_confirmation`.
         *
         *     **The card is resolved against the catalog before it is written.** The identifier arrives from a client and is therefore not trusted (spec §55); one that names no card is refused with the same `card_not_identified` that `GET /cards/{id}` answers with.
         *
         *     Only an analysis waiting for a confirmation can take one, and there is no way back: spec §65's states move forwards only, so confirming twice — or confirming a different card afterwards — is a 409. A card chosen in error is corrected by starting a new analysis.
         */
        post: operations["confirm_card_analyses__analysis_id__confirm_card_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/analyses/{analysis_id}/economic-configuration": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Configure the economics of one analysis
         * @description Records spec §46's cost line items, §45's optional acquisition cost, the grading companies to compare and §43's optimization mode, and attaches them to the analysis as the immutable configuration spec §57's reproducibility record names.
         *
         *     **Absent is not zero.** Omitting `acquisition_cost` means the user did not say, and the investment figures are then reported as `null` with `acquisition_cost_not_supplied`. `"0.00"` is a real acquisition cost. Nothing infers one.
         *
         *     **A configuration is written once.** Spec §5 puts this step immediately after card confirmation, so an analysis takes one while it is `analyzing`; a second submission is a 409, and pricing the card differently is a new analysis rather than an edit.
         */
        post: operations["configure_economics_analyses__analysis_id__economic_configuration_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/analyses/{analysis_id}/images": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Upload one side of the card
         * @description Accepts one photograph as the request body — the raw image bytes, not a multipart form. The `side` says which view of the card it is; a second upload for the same side replaces the first, so a retake is a correction rather than an extra image.
         *
         *     **The file is validated by its content, never by what the request claims** (spec §55). It must be a JPEG or a PNG, must be within the byte and pixel limits this deployment is configured with, and must decode. Personal metadata — EXIF, including GPS — is removed before anything is stored, and the storage location is generated by the server: no filename is accepted, so none can influence it.
         *
         *     The analysis moves to `uploading` on the first photograph and to `uploaded` once both sides have arrived, which is the state `POST /analyses/{id}/run` requires.
         */
        post: operations["upload_image_analyses__analysis_id__images_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/analyses/{analysis_id}/results": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * The economics and recommendation for one analysis
         * @description Spec §64's results endpoint: §6's economics per company, §41's **two separately named** profit figures, ADR 0007's two ratios, the full grade distribution (§2.1) and §44's recommendation.
         *
         *     **Nothing is conflated.** `incremental_grading_decision` answers 'should I grade the card I own?' and `investment_return` answers 'did buying it to grade make money?'. They share no field name, and neither ratio is called `roi`.
         *
         *     **`companies` is empty and `recommendation` is `null` until the analysis has been calculated.** No milestone predicts a grade distribution yet, so that is today's answer for every analysis. It is an empty result rather than an error because the analysis is fine — it simply has not got there.
         *
         *     `Cache-Control: no-store`: every figure here descends from prices whose confidence is discounted for age at the moment of asking.
         */
        get: operations["read_results_analyses__analysis_id__results_get"];
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
    "/cards/{card_id}/market": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Read one card's market prices from a snapshot
         * @description Spec §64's market endpoint. Returns the ungraded price and every grade every supported company can issue, each with spec §38's `price_confidence` and `price_age` — **per price**, because a fresh raw price and a six-week-old PSA 10 price on the same card is the gap that matters. Served entirely from a market snapshot: no provider is called during a request (spec §37), and the snapshot's `data_version` is returned so a result can be shown for the date it describes rather than as today's. A price this snapshot does not hold is `null` rather than absent, and is never filled in from another company or interpolated. Pass `?snapshot_id=` to re-read exactly what a past analysis saw. No price history and no fees: the first is out under ADR 0006, the second is spec §45's user-configured economic input.
         */
        get: operations["read_card_market_cards__card_id__market_get"];
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
    "/grading-companies": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List the grading companies and their grade scales
         * @description Spec §64's grading endpoint. Returns every supported company with the exact grades it can issue and the version of its published standard in force today (spec §23), so a result can be tied back to it. **Render the scale from `grades` rather than hard-coding one**: PSA and TAG issue no 9.5 and BGS does, so a shared scale misrenders one of them, and a company added post-V1 appears here with no frontend change. Slow-moving reference data — the response carries `Cache-Control: public, max-age=3600`. No fees: spec §45's grading costs are user-configured economic inputs, not fetched here.
         */
        get: operations["list_grading_companies_grading_companies_get"];
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
    "/internal/annotation/images": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List the training images awaiting annotation
         * @description **Not part of spec §64.** The internal annotation surface (ADR 0009) — in this application and in this schema because `apps/annotation` generates its types from it, and kept off the public origin by deployment topology rather than by being a second service. Lists the training images that carry neither a defect marker nor a centering measurement, oldest first. Both tables are checked, not one: spec §30's eleven features are split across two of them, so an image carrying only a measurement has been worked on. Ordered by `(created_at, id)` — a total order, so paging neither drops nor duplicates a row. An offset past the end is an empty page, never a 404.
         */
        get: operations["list_images_awaiting_annotation_internal_annotation_images_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/internal/annotation/images/{image_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Read one training image and the other views of its copy
         * @description **Not part of spec §64.** The internal annotation surface (ADR 0009) — in this application and in this schema because `apps/annotation` generates its types from it, and kept off the public origin by deployment topology rather than by being a second service. Returns one image, which representation can be shown for it, and the other photographs of the same physical copy — what a front/back toggle moves between. `siblings` is empty where the image names no physical copy, which is an honest answer rather than a gap.
         */
        get: operations["read_training_image_internal_annotation_images__image_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/internal/annotation/images/{image_id}/annotations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Record one annotator's work on one training image
         * @description **Not part of spec §64.** The internal annotation surface (ADR 0009) — in this application and in this schema because `apps/annotation` generates its types from it, and kept off the public origin by deployment topology rather than by being a second service. Write spec §30's corner, edge and surface markers and the centering measurement for one image, in **one transaction**.
         *
         *     **Append-only.** `trg_image_annotations_immutable` refuses an `UPDATE`, so there is no edit endpoint and there will not be one: a correction is a new annotation, and the current view of a corner is the newest row for it. Nothing is unique per image and per region, which is what makes that representable — and a surface has as many defects as it has.
         *
         *     **The annotator and the timestamp are the service's.** §30 asks that both be recorded automatically rather than typed, so the request carries neither; the annotator comes from `TCG_API_ANNOTATOR_ID` and the timestamp from the row's default. That is also what keeps `annotator_id`'s grammar — which spec §53 makes structural, by having no `@` in it — out of a client's reach.
         *
         *     **Coordinates need an artifact.** A bounding box and a centering ratio are both fractions of the standardized artifact; against a photograph no card was located in, they mean nothing. Sending either for an image whose `has_artifact` is false is a 409. A marker with no box is still accepted there, because a corner's region names its position.
         *
         *     Recording anything takes the image off `GET /internal/annotation/images`.
         */
        post: operations["record_image_annotations_internal_annotation_images__image_id__annotations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/internal/annotation/images/{image_id}/bytes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Serve one representation of a training image
         * @description **Not part of spec §64.** The internal annotation surface (ADR 0009) — in this application and in this schema because `apps/annotation` generates its types from it, and kept off the public origin by deployment topology rather than by being a second service. Serves the bytes themselves, read through ADR 0002's `ObjectStorage` port. `representation=normalized` is the standardized artifact and 404s where none was stored — deliberately, rather than substituting the photograph: the caller has already been told which representation exists, and a silent substitution would hand a client a frame whose coordinates mean nothing. `Cache-Control: private, no-store`.
         */
        get: operations["read_training_image_bytes_internal_annotation_images__image_id__bytes_get"];
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
         *     the client holds a token, not a row id. Spec §57's reproducibility record is
         *     reported whole, in one nested object, rather than as fields scattered
         *     through this one: it is a single claim about how an answer was produced, and
         *     a caller checking whether an analysis can be reproduced should not have to
         *     assemble it. #35 adds the states this can hold, #104 fills `card_id`.
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
             * Images
             * @description Every photograph uploaded so far and spec §19's verdict on it. Empty before the first upload. This is how a `poor` verdict reaches the user, which §19 requires — a gate whose warning nothing surfaces is not a gate.
             */
            images: components["schemas"]["ImageQualityResponse"][];
            /** @description Spec §57's record: which versions of everything this answer was produced against, captured when the run claimed the analysis and immutable afterwards. */
            reproducibility: components["schemas"]["ReproducibilityResponse"];
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
         * AnnotationImageResponse
         * @description One training image, with the other photographs of the same physical copy.
         */
        AnnotationImageResponse: {
            /**
             * Annotations
             * @description Every marker recorded against this image, oldest first. **Not collapsed to a current reading**: both annotation tables are append-only, so a correction is a newer row, and a surface has as many defects as it has. The work list excludes an annotated image, so this endpoint is the only way one is ever seen again.
             */
            annotations: components["schemas"]["StoredMarkerResponse"][];
            /**
             * Card Id
             * @description Which catalog card it depicts.
             */
            card_id?: string | null;
            /**
             * Centering
             * @description Every centering measurement recorded against this image, oldest first.
             */
            centering: components["schemas"]["StoredMeasurementResponse"][];
            /**
             * Created At
             * Format: date-time
             * @description When the image was ingested.
             */
            created_at: string;
            /**
             * Has Artifact
             * @description Whether a standardized artifact was stored, and therefore which representation `…/bytes` can serve. False means all there is to show is the photograph — and a tool showing it must label it, because coordinates cannot be taken against it. The same field every summary carries, so a detail is a summary with more on it rather than a second shape.
             */
            has_artifact: boolean;
            /**
             * Height
             * @description The stored **photograph's** height in pixels.
             */
            height: number;
            /**
             * Id
             * Format: uuid
             * @description The training image's identifier.
             */
            id: string;
            /**
             * Physical Copy Id
             * @description Which physical object it is a photograph of.
             */
            physical_copy_id?: string | null;
            /**
             * Siblings
             * @description Other photographs of the same physical copy — what the front/back toggle moves between. **Empty when `physical_copy_id` is null**, which is an honest answer rather than a gap: treating null as a group would make every consented upload a sibling of every other one.
             */
            siblings: components["schemas"]["AnnotationImageSummary"][];
            /**
             * Side
             * @description Which view of the card this is.
             * @example front
             */
            side: string;
            /**
             * Source
             * @description Which ADR 0008 source class it came from.
             */
            source: string;
            /**
             * Width
             * @description The stored **photograph's** width in pixels.
             */
            width: number;
        };
        /**
         * AnnotationImageSummary
         * @description One training image, as the work list and an image's siblings report it.
         */
        AnnotationImageSummary: {
            /**
             * Card Id
             * @description Which catalog card it depicts, or null where nobody has identified it.
             */
            card_id?: string | null;
            /**
             * Created At
             * Format: date-time
             * @description When the image was ingested.
             */
            created_at: string;
            /**
             * Has Artifact
             * @description Whether a standardized artifact has been stored for it. False means the normalization pass has not run, or found no card — the tool then shows the photograph and must say so, because a coordinate taken against a photograph is not comparable with one taken against an artifact. The storage key itself is deliberately not reported: it is server-generated and internal (spec §55).
             */
            has_artifact: boolean;
            /**
             * Id
             * Format: uuid
             * @description The training image's identifier.
             */
            id: string;
            /**
             * Physical Copy Id
             * @description Which physical object it is a photograph of. **Null is an honest answer**: a consented upload identifies no copy (ADR 0008's approved class 4).
             */
            physical_copy_id?: string | null;
            /**
             * Side
             * @description Which view of the card this is — spec §30's front/back, and the same vocabulary an uploaded analysis uses. Six values, not two: a corpus may hold angled and surface views of the same copy.
             * @example front
             */
            side: string;
            /**
             * Source
             * @description Which ADR 0008 source class it came from.
             * @example first_party
             */
            source: string;
        };
        /**
         * AnnotationRequest
         * @description One annotator's work on one image, written in one transaction.
         *
         *     **One image per request, deliberately.** A marker belongs to the image whose
         *     artifact its coordinates are fractions of, and `training_images.side` is what
         *     says which face that is — accepting two images here would make it possible to
         *     file the back's corners against the front.
         *
         *     Carries **no annotator and no timestamp**: spec §30 asks that both be recorded
         *     automatically rather than typed, so the service supplies them. That is also
         *     what puts `image_annotations.annotator_id`'s grammar out of a client's reach.
         *
         *     Carries no `polygon` and no `metadata` either. Both are storable and read by
         *     nothing (#158); a polygon is in the same fractional space, so accepting one
         *     would mean it joined the artifact gate below for a control nothing draws yet.
         */
        AnnotationRequest: {
            /** @description The centering measurement for this image, if one was taken. */
            centering?: components["schemas"]["CenteringReadingRequest"] | null;
            /**
             * Markers
             * @description Corner, edge and surface markers to record.
             */
            markers?: (components["schemas"]["CornerMarkerRequest"] | components["schemas"]["EdgeMarkerRequest"] | components["schemas"]["SurfaceMarkerRequest"])[];
        };
        /**
         * AnnotationResponse
         * @description What one save wrote.
         *
         *     **Oldest first and not collapsed to a current reading.** Both tables are
         *     append-only, so a correction is a newer row — but a surface has as many
         *     defects as it has, so no one collapsing rule fits all three kinds. The rows
         *     travel as they are.
         */
        AnnotationResponse: {
            /**
             * Centering
             * @description The centering measurements that were stored.
             */
            centering: components["schemas"]["StoredMeasurementResponse"][];
            /**
             * Markers
             * @description The markers that were stored.
             */
            markers: components["schemas"]["StoredMarkerResponse"][];
        };
        /**
         * AnnotationWorkListResponse
         * @description The images awaiting annotation, one page at a time.
         */
        AnnotationWorkListResponse: {
            /**
             * Images
             * @description This page of images, oldest first.
             */
            images: components["schemas"]["AnnotationImageSummary"][];
            /**
             * Limit
             * @description The page size that was applied.
             */
            limit: number;
            /**
             * Offset
             * @description The offset that was applied.
             */
            offset: number;
            /**
             * Total
             * @description How many images await annotation in total. **This number falls as annotations land**, so a page boundary can move underneath a client that is annotating while it pages.
             */
            total: number;
        };
        /**
         * BoundingBoxModel
         * @description Spec §17's bounding box, as fractions of the normalized artifact.
         *
         *     **Fractions, never pixels.** The artifact's resolution is `ml/normalization`'s
         *     and appears nowhere in this service — a fraction survives a change to it, and
         *     a pixel would not.
         *
         *     One object rather than four fields, because the schema's rule is
         *     `num_nulls(bbox_x, bbox_y, bbox_width, bbox_height) IN (0, 4)`: a box is whole
         *     or absent, and an optional object is that rule in a request body.
         */
        BoundingBoxModel: {
            /**
             * Height
             * @description Height, and positive.
             * @example 0.08
             */
            height: number;
            /**
             * Width
             * @description Width, and positive.
             * @example 0.08
             */
            width: number;
            /**
             * X
             * @description Distance from the left edge.
             * @example 0.02
             */
            x: number;
            /**
             * Y
             * @description Distance from the top edge.
             * @example 0.03
             */
            y: number;
        };
        /**
         * CardConfirmationRequest
         * @description Which card the user says they are holding — spec §20, §64.
         *
         *     One field, and deliberately only one. The catalog record is the truth about
         *     what that card is, so a client that also sent a name, a set or a variant
         *     would be sending something this service must not believe (spec §55: never
         *     trust client-side card metadata). The identifier is resolved against the
         *     catalog before it is written, which is what makes it a card rather than a
         *     string the caller chose.
         */
        CardConfirmationRequest: {
            /**
             * Card Id
             * Format: uuid
             * @description The card the user confirmed, from `GET /cards/search` or `GET /cards/{id}`.
             */
            card_id: string;
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
         * CardMarketResponse
         * @description The body of a successful `GET /cards/{card_id}/market`.
         */
        CardMarketResponse: {
            /**
             * Card Id
             * Format: uuid
             * @description The card these prices are for.
             */
            card_id: string;
            /**
             * Graded
             * @description Every grade every supported company can issue, in the order `GET /grading-companies` lists them and ascending within each. The full ladder every time, holes included — spec §6's price panel is read down it, and spec §39's expected value is summed over it.
             */
            graded: components["schemas"]["GradedPriceResponse"][];
            /** @description The ungraded market price, or `null` when the snapshot holds none. */
            raw: components["schemas"]["PriceResponse"] | null;
            /** @description The snapshot they were read from. Never null — see the 404 and 503. */
            snapshot: components["schemas"]["MarketSnapshotResponse"];
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
         * CenteringReadingRequest
         * @description One centering measurement — spec §21, §13.
         *
         *     §13 requires ratios rather than qualitative labels, and the direction is
         *     stated once so a number cannot mean two things: `horizontal` is the **left**
         *     border as a fraction of the two side borders together, `vertical` the **top**
         *     of the two ends. `0.5` is perfect centering.
         *
         *     The tool derives both from where the annotator put the inner frame, because
         *     an annotator typing a ratio is an annotator doing arithmetic under time
         *     pressure.
         */
        CenteringReadingRequest: {
            /**
             * Confidence
             * @description §30's uncertainty, required here exactly as on a marker. A border read off a worn or glare-lit edge is a real measurement with a low confidence, and recording it at 1.0 would be a fabricated certainty.
             * @example 0.9
             */
            confidence: number;
            /**
             * Horizontal
             * @description left / (left + right). **Null where the axis has no measurable border** — §21 names full-art and borderless layouts outright, and inventing 0.5 for one of them is the confidently-wrong output spec §2.7 exists to forbid.
             * @example 0.52
             */
            horizontal?: number | null;
            /**
             * Notes
             * @description Free text — in practice, which of §21's awkward layouts this card is and what was measured against. Not one of §30's eleven and not a vocabulary: template awareness is M7's model, and this is the human's note to it.
             */
            notes?: string | null;
            /**
             * Vertical
             * @description top / (top + bottom), on the same terms.
             * @example 0.49
             */
            vertical?: number | null;
        };
        /**
         * CompanyComparisonResponse
         * @description Spec §49's compare table, in the order the chosen mode produced.
         */
        CompanyComparisonResponse: {
            /** Label */
            label: string;
            /** Mode */
            mode: string;
            /** Ranked */
            ranked: components["schemas"]["RankedCompanyResponse"][];
            /**
             * Tied At The Top
             * @description Companies that tied for first. The order among them is alphabetical and **means nothing** — say so rather than presenting an arbitrary winner.
             */
            tied_at_the_top: string[];
            /** Unranked */
            unranked: components["schemas"]["UnrankedCompanyResponse"][];
        };
        /**
         * CompanyEconomicsResponse
         * @description Every M5 figure for one grading company.
         *
         *     Four figures, four reasons. Each figure is `null` when it could not be
         *     computed and its reason says which question could not be asked —
         *     `no_raw_price_available`, `no_graded_price_available`,
         *     `acquisition_cost_not_supplied`, `no_capital_at_risk`. Present-and-null
         *     beside a reason, never omitted, and never zero.
         */
        CompanyEconomicsResponse: {
            /**
             * Company
             * @example psa
             */
            company: string;
            costs: components["schemas"]["CostConfigurationResponse"];
            /**
             * Distribution Confidence
             * @description How far this company's model is trusted. Never assumed.
             */
            distribution_confidence: number;
            /** @description Spec §43's `expected_graded_value` — ADR 0007's `graded_proceeds`, **net of the selling fee**, the fee applied inside the sum. */
            expected_graded_value: components["schemas"]["ExpectedValueResponse"] | null;
            /**
             * Expected Graded Value Reason
             * @description Why there is no expectation, when there is none.
             */
            expected_graded_value_reason: string | null;
            /**
             * Grade Distribution
             * @description **The full distribution, always** — spec §2.1 retains it even when a UI shows one number. Ascending by grade.
             */
            grade_distribution: components["schemas"]["GradeProbabilityResponse"][];
            incremental_grading_decision: components["schemas"]["IncrementalGradingDecisionResponse"] | null;
            /** Incremental Reason */
            incremental_reason: string | null;
            incremental_roi: components["schemas"]["RatioResponse"] | null;
            /** Incremental Roi Reason */
            incremental_roi_reason: string | null;
            /** Investment Reason */
            investment_reason: string | null;
            investment_return: components["schemas"]["InvestmentReturnResponse"] | null;
            investment_roi: components["schemas"]["RatioResponse"] | null;
            /**
             * Investment Roi Reason
             * @description `acquisition_cost_not_supplied` when the user did not say what they paid — ADR 0007's own string, and never a zero standing in for it.
             */
            investment_roi_reason: string | null;
        };
        /**
         * ConditionVerdict
         * @description What the gate was able to say about one condition.
         *
         *     Three values rather than a boolean, because the third is the point. A gate
         *     that reported "no glare" when it had not looked for glare would be the
         *     confidently-wrong output spec §2.7 exists to forbid.
         * @enum {string}
         */
        ConditionVerdict: "clear" | "detected" | "undetermined";
        /**
         * CornerLabel
         * @description Spec §14's potential corner labels, in the specification's order.
         * @enum {string}
         */
        CornerLabel: "clean" | "whitening" | "rounding" | "chipping" | "dent" | "crease" | "layering" | "unknown";
        /**
         * CornerMarkerRequest
         * @description One corner — spec §14. Four regions, not eight: the side is the image's.
         */
        CornerMarkerRequest: {
            /** @description §17's spatial data, where the annotator drew it. Optional: a corner's region already names its position, so `top_left: clean` is a complete annotation. **Only meaningful against a stored artifact** — see the endpoint's 409. */
            bbox?: components["schemas"]["BoundingBoxModel"] | null;
            /**
             * Confidence
             * @description §30's uncertainty — how sure the annotator is of this call. **Required, with no default**: a default would read as certainty for every row nobody supplied one for, which is the fabricated confidence spec §2.7 forbids. The other half of the same rule is the `unknown` label every vocabulary carries.
             * @example 0.8
             */
            confidence: number;
            /**
             * @description Spec §30's corner annotation. (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            kind: "corner";
            /**
             * @description §14's eight potential labels. **Not §15's** — a corner cannot be `rough_cut` or `notching`, which are cutting defects of an edge.
             * @example whitening
             */
            label: components["schemas"]["CornerLabel"];
            /**
             * @description Which corner. §14 lists eight, front- and back-prefixed; the prefix is `training_images.side`, because the image already knows which face it shows and naming it twice would let the two disagree.
             * @example top_left
             */
            region: components["schemas"]["CornerRegion"];
            /**
             * @description How bad it is — an **ordinal**, because there is one annotator and no agreement study, so finer granularity would record a precision nobody could reproduce. Null exactly when the label asserts no defect (`clean` found nothing to rate, `unknown` could not rate what it found), and required otherwise.
             * @example minor
             */
            severity?: components["schemas"]["DefectSeverity"] | null;
        };
        /**
         * CornerRegion
         * @description Which corner — spec §14, without the side prefix.
         *
         *     Reading order, which is also the order §14 lists them within a side.
         * @enum {string}
         */
        CornerRegion: "top_left" | "top_right" | "bottom_left" | "bottom_right";
        /**
         * CostConfigurationRequest
         * @description Spec §46's six line items. **Never a total** — #58 binds that there is none.
         *
         *     Every field has the engine's own default, so a client that has nothing to say
         *     about shipping does not have to invent a number, and the defaults live in one
         *     place rather than being restated in `apps/web`. They are illustrative
         *     placeholders and deliberately non-zero: an all-zero configuration reports
         *     grading as costless and tilts every recommendation toward *grade*.
         */
        CostConfigurationRequest: {
            /**
             * Grading Fee
             * @example 40.00
             */
            grading_fee?: number | string;
            /**
             * Insurance
             * @example 40.00
             */
            insurance?: number | string;
            /**
             * Miscellaneous
             * @example 40.00
             */
            miscellaneous?: number | string;
            /**
             * Outbound Shipping
             * @example 40.00
             */
            outbound_shipping?: number | string;
            /**
             * Return Shipping
             * @example 40.00
             */
            return_shipping?: number | string;
            selling_fee?: components["schemas"]["SellingFeeRequest"];
        };
        /**
         * CostConfigurationResponse
         * @description Spec §46's line items as stored. **There is no total, by design.**
         *
         *     §47's future dimensions — country, tax, service tier, shipping provider —
         *     attach to individual lines, so a total is a figure that would have to be
         *     unpicked again. A client that wants one adds five numbers and knows which
         *     five it added; the selling fee is not one of them, because ADR 0007 nets it
         *     out of proceeds rather than committing it up front.
         */
        CostConfigurationResponse: {
            /** Grading Fee */
            grading_fee: string;
            /** Insurance */
            insurance: string;
            /** Miscellaneous */
            miscellaneous: string;
            /** Outbound Shipping */
            outbound_shipping: string;
            /** Return Shipping */
            return_shipping: string;
            selling_fee: components["schemas"]["SellingFeeResponse"];
        };
        /**
         * DefectSeverity
         * @description How bad a defect is — spec §17's `severity`, which §17 does not define.
         *
         *     An ordinal rather than a number in ``[0, 1]``, and that is a decision about
         *     who is answering. There is one annotator and no inter-annotator agreement
         *     study (§30's feature list has neither), so a continuous scale would record a
         *     precision nobody could reproduce — and a model fitting that precision fits
         *     noise. Three levels are reproducible; M8 may map them to numbers, which is a
         *     modelling choice made where the model lives.
         * @enum {string}
         */
        DefectSeverity: "minor" | "moderate" | "severe";
        /**
         * EconomicConfigurationRequest
         * @description What the user says the economics of their decision are — spec §45, §46, §43.
         */
        EconomicConfigurationRequest: {
            /**
             * Acquisition Cost
             * @description What the user paid, if they said. **Absent is not zero**: `null` means they did not say and is reported as `acquisition_cost_not_supplied`, while `"0.00"` is a real acquisition cost — a raffle win, a pull from somebody else's pack. Spec §45 forbids inferring it, so nothing here fills it in from the market price.
             * @example 120.00
             */
            acquisition_cost?: (number | string) | null;
            costs?: components["schemas"]["CostConfigurationRequest"];
            /**
             * Grading Companies
             * @description Which companies to compare, as the slugs `GET /grading-companies` uses. At least one, and no duplicates — two entries for one company would list it twice and make 'best' meaningless.
             * @example [
             *       "psa",
             *       "bgs"
             *     ]
             */
            grading_companies: string[];
            /**
             * Optimization Mode
             * @description Spec §43's optimization mode: `expected_profit`, `roi`, `highest_grade_probability`, `lowest_total_cost` or `expected_graded_value`. **`roi` is a mode name, never a figure** — the results name two ratios and neither is called `roi`.
             * @example expected_profit
             */
            optimization_mode: string;
        };
        /**
         * EconomicConfigurationResponse
         * @description One stored configuration, read back exactly as it was written.
         */
        EconomicConfigurationResponse: {
            /**
             * Acquisition Cost
             * @description What the user paid, or `null` if they did not say. **`null` is not `"0.00"`** — the second is a real acquisition cost, and the two reach different §41 answers.
             */
            acquisition_cost: string | null;
            costs: components["schemas"]["CostConfigurationResponse"];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Currency
             * @description ISO 4217 code for every amount in this object.
             * @example SGD
             */
            currency: string;
            /** Grading Companies */
            grading_companies: string[];
            /**
             * Id
             * Format: uuid
             * @description Spec §57's `economic_configuration`. Immutable: an analysis references this identifier for as long as it exists, and pricing the card differently is a new analysis rather than an edit.
             */
            id: string;
            /** Optimization Mode */
            optimization_mode: string;
            thresholds: components["schemas"]["RecommendationThresholdsResponse"];
        };
        /**
         * EdgeLabel
         * @description Spec §15's potential edge labels, in the specification's order.
         *
         *     Deliberately **not** :class:`CornerLabel`: §15 adds `rough_cut` and
         *     `notching`, which are cutting defects a corner does not have, and drops
         *     `rounding` and `crease`, which are not edge failures. Collapsing the two into
         *     one list would make a corner annotable as `rough_cut`.
         * @enum {string}
         */
        EdgeLabel: "clean" | "whitening" | "chipping" | "rough_cut" | "notching" | "layering" | "dent" | "unknown";
        /**
         * EdgeMarkerRequest
         * @description One edge — spec §15.
         */
        EdgeMarkerRequest: {
            /** @description §17's spatial data, where the annotator drew it. Optional: a corner's region already names its position, so `top_left: clean` is a complete annotation. **Only meaningful against a stored artifact** — see the endpoint's 409. */
            bbox?: components["schemas"]["BoundingBoxModel"] | null;
            /**
             * Confidence
             * @description §30's uncertainty — how sure the annotator is of this call. **Required, with no default**: a default would read as certainty for every row nobody supplied one for, which is the fabricated confidence spec §2.7 forbids. The other half of the same rule is the `unknown` label every vocabulary carries.
             * @example 0.8
             */
            confidence: number;
            /**
             * @description Spec §30's edge annotation. (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            kind: "edge";
            /**
             * @description §15's eight potential labels. **Not §14's** — an edge does not round or crease, and it does have `rough_cut` and `notching`.
             * @example rough_cut
             */
            label: components["schemas"]["EdgeLabel"];
            /**
             * @description Which edge, clockwise from the top.
             * @example left
             */
            region: components["schemas"]["EdgeRegion"];
            /**
             * @description How bad it is — an **ordinal**, because there is one annotator and no agreement study, so finer granularity would record a precision nobody could reproduce. Null exactly when the label asserts no defect (`clean` found nothing to rate, `unknown` could not rate what it found), and required otherwise.
             * @example minor
             */
            severity?: components["schemas"]["DefectSeverity"] | null;
        };
        /**
         * EdgeRegion
         * @description Which edge — spec §15, clockwise from the top.
         *
         *     §15 names no positions at all; it says only to represent front and back
         *     separately. Four edges is this project's, and clockwise from the top is
         *     :data:`~tcg_domain.card_geometry.CORNER_NAMES`' order, so a reader who knows
         *     one knows the other.
         * @enum {string}
         */
        EdgeRegion: "top" | "right" | "bottom" | "left";
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
         * ExpectedValueResponse
         * @description Spec §40's expectation, and what it could not see.
         */
        ExpectedValueResponse: {
            /**
             * Amount
             * @description The expectation **conditional on a priced grade occurring**: an unpriced grade is excluded and the rest renormalised, never valued at zero.
             * @example 234.00
             */
            amount: string;
            /** Confidence */
            confidence: number;
            /**
             * Unpriced Grades
             * @description Which grades the snapshot held no price for. Empty is the good case.
             */
            unpriced_grades: string[];
            /**
             * Unpriced Probability
             * @description How much of the distribution those grades carried.
             */
            unpriced_probability: number;
        };
        /**
         * GradeProbabilityResponse
         * @description One term of a grade distribution — spec §2.1's `P(g)`.
         */
        GradeProbabilityResponse: {
            /**
             * Grade
             * @description A grade key, spelled as `GET /grading-companies` spells it. A collapsed tail such as `7_or_lower` is a grade key too.
             * @example 10
             */
            grade: string;
            /** Probability */
            probability: number;
        };
        /**
         * GradedPriceResponse
         * @description What a card graded by one company at one grade is worth.
         */
        GradedPriceResponse: {
            /**
             * Company
             * @description The company's lowercase slug, as `GET /grading-companies` spells it.
             * @example psa
             */
            company: string;
            /**
             * Grade
             * @description A grade on that company's scale, spelled exactly as `GET /grading-companies` spells it. The two agree by construction.
             * @example 10
             */
            grade: string;
            /** @description `null` when this snapshot holds no price for this company and grade — which is a fact about the data, never a substituted or interpolated figure from another company. Present-and-null rather than omitted, so a client never has to tell a gap apart from a missing field. */
            price: components["schemas"]["PriceResponse"] | null;
        };
        /**
         * GradingCompaniesResponse
         * @description The body of a successful `GET /grading-companies`.
         */
        GradingCompaniesResponse: {
            /**
             * Companies
             * @description Every company the product supports, in a stable order. A company added post-V1 appends to it.
             */
            companies: components["schemas"]["GradingCompanyResponse"][];
        };
        /**
         * GradingCompanyResponse
         * @description One grading company, as a client needs to render it.
         */
        GradingCompanyResponse: {
            /**
             * Company
             * @description The company's lowercase slug. The key a graded price is stored under.
             * @example psa
             */
            company: string;
            /**
             * Display Name
             * @description What to show a user.
             * @example PSA
             */
            display_name: string;
            /**
             * Grades
             * @description Every grade this company can issue, ascending. Not shared between companies: PSA and TAG have no 9.5 and BGS does. Render from this list rather than from a hard-coded scale.
             * @example [
             *       "1",
             *       "1.5",
             *       "2",
             *       "8.5",
             *       "9",
             *       "10"
             *     ]
             */
            grades: string[];
            /** @description The published standard in force today, or `null` when no version of this company's standard has been recorded. */
            rules: components["schemas"]["GradingRulesResponse"] | null;
        };
        /**
         * GradingRulesResponse
         * @description The version of one company's published standard currently in force.
         *
         *     Spec §23's record, minus the `rules` body — that is empty in V1 by decision
         *     (#46: the published standards are the companies' copyrighted text, and what
         *     §57 needs is the identifier plus a source a human can open).
         */
        GradingRulesResponse: {
            /**
             * Effective From
             * @description When the company's published standard took effect, where the company states one. `null` where it states none — never a guess.
             * @example 2008-02-01
             */
            effective_from: string | null;
            /**
             * Effective To
             * @description When it stopped applying, or `null` while it is current. Derived from the next version's start rather than stored, so two versions of one company cannot overlap.
             * @example null
             */
            effective_to: string | null;
            /**
             * Source
             * @description Where the standard was read. A URL a human can open.
             * @example https://www.psacard.com/gradingstandards
             */
            source: string;
            /**
             * Verified On
             * Format: date
             * @description When the source was last read.
             * @example 2026-08-24
             */
            verified_on: string;
            /**
             * Version
             * @description The identifier an analysis retains — spec §57's `grading_rules_version`. No grading company publishes a version for its standard, so this one is this repository's, stamped with the date the standard was read.
             * @example psa-rules-2026-08-24
             */
            version: string;
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
         * ImageQualityResponse
         * @description One uploaded photograph and what spec §19's gate made of it.
         */
        ImageQualityResponse: {
            /**
             * Findings
             * @description All eleven of spec §19's conditions, or empty while the gate has not run. Never a subset: a condition nobody assessed is reported `undetermined` rather than omitted.
             */
            findings: components["schemas"]["QualityFindingResponse"][];
            /**
             * Quality Score
             * @description A [0, 1] verdict, 1 being best, or null while the gate has not run. The smallest headroom any condition had, so one bad measurement is not averaged away by five good ones.
             */
            quality_score: number | null;
            /** @description Spec §19's verdict, or null while the gate has not run. `unusable` means the analysis stopped; `poor` means it went on and the user must be told. */
            quality_status: components["schemas"]["QualityStatus"] | null;
            /** @description Which view of the card this is. */
            side: components["schemas"]["ImageSide"];
        };
        /**
         * ImageResponse
         * @description One uploaded photograph, as the API reports it.
         *
         *     `apps/web` generates its types from this model (ADR 0001), so the field
         *     names are a public contract.
         *
         *     The derived columns — `normalized_uri`, `width`, `height`, `quality_score`,
         *     `quality_status` — are deliberately absent: nothing has computed one yet,
         *     and a field that is always null is an invitation to render an empty value.
         *     `analysis_status` is present because the caller has just caused the
         *     transition and would otherwise need a second request to learn whether the
         *     analysis can now be run.
         */
        ImageResponse: {
            /**
             * Analysis Id
             * Format: uuid
             * @description The analysis this photograph belongs to.
             */
            analysis_id: string;
            /**
             * Analysis Status
             * @description The analysis's state after this upload. `uploading` until every side has arrived, then `uploaded`, which is the state `POST /analyses/{id}/run` requires.
             * @example uploading
             */
            analysis_status: string;
            /**
             * Created At
             * Format: date-time
             * @description When this photograph was accepted.
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             * @description This service's identifier for the image.
             */
            id: string;
            /**
             * Mime Type
             * @description The type the file was validated as, read from its content. Never the type the request declared (spec §55).
             * @example image/jpeg
             */
            mime_type: string;
            /**
             * Sha256
             * @description A digest over the bytes that were stored — which are the uploaded bytes with their metadata removed, not the bytes as they arrived.
             */
            sha256: string;
            /**
             * Side
             * @description Which view of the card this is.
             * @example front
             */
            side: string;
        };
        /**
         * ImageSide
         * @description Which view of the card an image is — spec §11.
         *
         *     All six exist from the first migration. Only :data:`V1_SIDES` are written in
         *     V1; the other four are spec §52's guided-photography flow, which the V1
         *     image pipeline "must be compatible with". Admitting them now costs a longer
         *     CHECK constraint and saves a migration against a table holding user data.
         * @enum {string}
         */
        ImageSide: "front" | "back" | "angled_front" | "angled_back" | "surface_front" | "surface_back";
        /**
         * IncrementalGradingDecisionResponse
         * @description Spec §41's first figure: **should I grade the card I already own?**
         *
         *     The acquisition cost is a sunk cost here and cannot reach this figure — there
         *     is no field for it, which is how the engine keeps it out and how this keeps
         *     it out. Compare `InvestmentReturnResponse`, which answers a different
         *     question with different numbers; the two share no field name on purpose.
         */
        IncrementalGradingDecisionResponse: {
            /** Confidence */
            confidence: number;
            /**
             * Graded Proceeds
             * @description Sum over grades of P(g)*(V(g) less the selling fee on V(g)), the fee applied per outcome.
             */
            graded_proceeds: string;
            /**
             * Grading Costs
             * @description Five of spec §46's six line items. The selling fee is deliberately not among them: ADR 0007 nets it out of proceeds rather than counting it as capital committed up front.
             */
            grading_costs: string;
            /**
             * Incremental Profit
             * @description Expected graded proceeds, less the raw-sale opportunity value, less grading costs. **A negative figure is an answer**, not an error: it means selling the card raw is the better move.
             * @example 24.00
             */
            incremental_profit: string;
            /**
             * Raw Market Value
             * @description What the card fetches ungraded, gross.
             */
            raw_market_value: string;
            /**
             * Raw Opportunity Value
             * @description The raw sale, net of its own selling fee. **Both branches pay the fee** — charging it only to the graded side is a systematic bias toward grading.
             */
            raw_opportunity_value: string;
            /**
             * Raw Selling Fee
             * @description What selling it raw would cost.
             */
            raw_selling_fee: string;
            /** Unpriced Grades */
            unpriced_grades: string[];
            /** Unpriced Probability */
            unpriced_probability: number;
        };
        /**
         * InvestmentReturnResponse
         * @description Spec §41's second figure: **did buying this card to grade make money?**
         *
         *     Answerable only when the user said what they paid. Shares no field name with
         *     the incremental decision, so no client can render one under the other's
         *     label.
         */
        InvestmentReturnResponse: {
            /**
             * Acquisition Cost
             * @description What the user said they paid.
             */
            acquisition_cost: string;
            /** Confidence */
            confidence: number;
            /** Graded Proceeds */
            graded_proceeds: string;
            /** Grading Costs */
            grading_costs: string;
            /**
             * Investment Profit
             * @description Expected graded proceeds, less the acquisition cost, less grading costs.
             */
            investment_profit: string;
            /** Unpriced Grades */
            unpriced_grades: string[];
            /** Unpriced Probability */
            unpriced_probability: number;
        };
        /**
         * MarketSnapshotReference
         * @description Which cut of the market these economics were computed against — spec §36.
         */
        MarketSnapshotReference: {
            /**
             * Data Version
             * @description **Show this beside the figures.** A dated record of a past market is honest; the same numbers presented as current are not.
             * @example 2026-08-25
             */
            data_version: string;
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
        };
        /**
         * MarketSnapshotResponse
         * @description Which cut of the market these prices came from — spec §36.
         */
        MarketSnapshotResponse: {
            /**
             * Data Version
             * Format: date
             * @description The snapshot's date, generated from `generated_at`. **Show this beside the prices**: a dated record of a past market is honest, where the same figures presented as current are not.
             * @example 2026-08-25
             */
            data_version: string;
            /**
             * Generated At
             * Format: date-time
             * @description When the cut was taken. Every price here was stored at or before it.
             */
            generated_at: string;
            /**
             * Id
             * Format: uuid
             * @description Spec §57's `market_snapshot_id`. Pass it back as `?snapshot_id=` to read the same prices again however many ingestion runs have happened since.
             */
            id: string;
        };
        /**
         * PriceResponse
         * @description One price, with what it is currently worth believing.
         */
        PriceResponse: {
            /**
             * Amount
             * @description The amount, as an exact decimal string with two places. A string rather than a number: a JSON number is a float in most clients, and a rounding error in a figure a user is deciding money on is not acceptable. **Zero is a real price** — a card nobody will pay for — and is why an absent price is `null` rather than `0.00`.
             * @example 12.30
             */
            amount: string;
            /**
             * Currency
             * @description ISO 4217 code for `amount`.
             * @example SGD
             */
            currency: string;
            /**
             * Observed At
             * Format: date-time
             * @description When the provider saw this price. The instant `price_age_seconds` counts from.
             */
            observed_at: string;
            /**
             * Price Age Seconds
             * @description Spec §38's `price_age`: how long before this request the price was observed. Computed now rather than stored, which is why the response is not cached. A provider clock running ahead of ours reads as 0, not as a negative age.
             * @example 7200
             */
            price_age_seconds: number;
            /**
             * Price Confidence
             * @description Spec §38's `price_confidence`: how much the provider was sure of this figure, discounted for how long ago it was true. Flat at the provider's own number for a day, then falling to a floor above zero — old evidence is still evidence, and reporting it at zero would be indistinguishable from having none. The provider's undiscounted figure is deliberately not exposed: only this one is fit to show a user.
             * @example 0.86
             */
            price_confidence: number;
        };
        /**
         * QualityCondition
         * @description Spec §19's eleven conditions, in the order the specification lists them.
         *
         *     A closed list for the reason :class:`~tcg_domain.analysis.AnalysisStatus` is
         *     one: a condition nobody wrote a detector for is a condition no image can be
         *     refused for. A twelfth is a feature, and it arrives with the heuristic that
         *     decides it and the copy that explains it.
         * @enum {string}
         */
        QualityCondition: "blur" | "low_resolution" | "glare" | "poor_exposure" | "excessive_darkness" | "excessive_brightness" | "severe_perspective_distortion" | "card_partly_outside_frame" | "multiple_cards" | "sleeve_obstruction" | "insufficient_card_size";
        /**
         * QualityFindingResponse
         * @description What the gate concluded about one of spec §19's eleven conditions.
         *
         *     The measurement the gate recorded is deliberately absent. It exists so M7's
         *     model can be compared against this baseline, and a Laplacian variance is not
         *     something to put in front of somebody holding a phone; the copy that turns a
         *     condition into a sentence lives in `apps/web`.
         */
        QualityFindingResponse: {
            /**
             * @description Which of spec §19's eleven conditions this is about.
             * @example blur
             */
            condition: components["schemas"]["QualityCondition"];
            /** @description What a detected condition makes the image — `poor` or `unusable`. Null for every other verdict. Nullable rather than absent, so a reader never has to tell 'no severity' from 'field not sent'. */
            severity: components["schemas"]["QualityStatus"] | null;
            /**
             * @description `clear` if it was looked for and not found, `detected` if it was, `undetermined` if the gate could not tell. The third is a real answer, not a gap: five conditions need the card located first, which no V1 stage does yet.
             * @example clear
             */
            verdict: components["schemas"]["ConditionVerdict"];
        };
        /**
         * QualityStatus
         * @description What the image-quality gate concluded — spec §19.
         *
         *     §19 also fixes what each means for the pipeline: `unusable` stops the
         *     analysis, `poor` continues but the user must be told. `good` and
         *     `acceptable` proceed silently. The gate itself is an OpenCV heuristic in M2
         *     and a model in M7; this vocabulary does not change when it does.
         * @enum {string}
         */
        QualityStatus: "good" | "acceptable" | "poor" | "unusable";
        /** RankedCompanyResponse */
        RankedCompanyResponse: {
            /** Company */
            company: string;
            /** Confidence */
            confidence: number;
            /**
             * Figure
             * @description **What was ranked** — `incremental_roi`, `incremental_profit`, `grading_costs`, `graded_proceeds` or a `P(g)`. Never `roi`: §43's `roi` is a mode name and no figure carries it, so a comparison cannot be shown under a label its number does not match.
             * @example incremental_profit
             */
            figure: string;
            /**
             * Value
             * @description The figure this company was ranked on, as a decimal string.
             */
            value: string;
        };
        /**
         * RatioResponse
         * @description One of ADR 0007's two ratios. **Neither is ever called `roi` alone.**
         */
        RatioResponse: {
            /**
             * Capital At Risk
             * @description The denominator. **It includes the card**, which is why this number is smaller than figures quoted elsewhere: the numerator has already subtracted the raw-sale opportunity value, so a denominator omitting it would pretend the card is not committed. See ADR 0007.
             */
            capital_at_risk: string;
            /** Confidence */
            confidence: number;
            /**
             * Label
             * @description What to call this ratio on screen, from ADR 0007.
             * @example Return on grading
             */
            label: string;
            /**
             * Value
             * @description A ratio quantised to **four** places, as a decimal string. `"0.6250"` is 62.5%. Four rather than money's two because a ratio is not money, and a string for the same reason an amount is one.
             * @example 0.6250
             */
            value: string;
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
         * ReasonResponse
         * @description Why the recommendation is what it is — spec §44's `reason`.
         *
         *     **Four fields and no sentence.** Spec §50 forbids explanations unrelated to
         *     model evidence, and a reason that is nothing but the figure, its value and
         *     the threshold it was measured against cannot be unrelated to the evidence.
         *     The copy that turns this into English lives in `apps/web`; adding a message
         *     here would put a second, unverifiable explanation on the wire.
         */
        ReasonResponse: {
            /**
             * Code
             * @description What fired, as a stable machine name. Key your copy off this.
             * @example profit_clears_margin
             */
            code: string;
            /**
             * Figure
             * @description What was measured.
             * @example incremental_profit
             */
            figure: string;
            /**
             * Threshold
             * @description What it was measured against, on the same terms.
             */
            threshold: string | null;
            /**
             * Value
             * @description The number measured, as a decimal string. `null` when there was no number — a propagated admission is the absence of a figure, not a figure with a bad value.
             */
            value: string | null;
        };
        /**
         * RecommendationResponse
         * @description Spec §44's output. The mode picks the company; the economics pick the action.
         */
        RecommendationResponse: {
            comparison: components["schemas"]["CompanyComparisonResponse"] | null;
            /**
             * Comparison Reason
             * @description `no_company_can_be_ranked` when no company could be ordered at all.
             */
            comparison_reason: string | null;
            /**
             * Confidence
             * @description The weakest of the confidences that exist — a minimum, never a product.
             */
            confidence: number;
            /**
             * Failed Gates
             * @description Every gate that failed, not only the decisive one, so a user who fixes the first is not sent into a second wall nobody mentioned.
             */
            failed_gates: components["schemas"]["ReasonResponse"][];
            /** Figure Confidence */
            figure_confidence: number | null;
            /** Grade Confidence */
            grade_confidence: number | null;
            /** Image Quality */
            image_quality: number;
            reason: components["schemas"]["ReasonResponse"];
            /**
             * Recommended Action
             * @description `grade`, `do_not_grade` or `insufficient_information`.
             * @example grade
             */
            recommended_action: string;
            /**
             * Recommended Company
             * @description **`null` whenever the action is `insufficient_information`.** Naming a company beside 'we cannot tell' is exactly the forced recommendation §44 forbids — a screen shown both renders the company as the answer.
             */
            recommended_company: string | null;
        };
        /**
         * RecommendationThresholdsResponse
         * @description Where the answer changes — #64's five gates, as they stood for this analysis.
         *
         *     Reported rather than accepted: they are policy, not a card's costs. They are
         *     stored per configuration so a recommendation stays reproducible when M7/M8's
         *     calibration moves them, and they are shown so a user can see that
         *     "insufficient information" was a threshold being missed rather than an
         *     opinion.
         */
        RecommendationThresholdsResponse: {
            /** Maximum Unpriced Probability */
            maximum_unpriced_probability: number;
            /** Minimum Figure Confidence */
            minimum_figure_confidence: number;
            /** Minimum Grade Confidence */
            minimum_grade_confidence: number;
            /** Minimum Image Quality */
            minimum_image_quality: number;
            /**
             * Minimum Incremental Profit
             * @example 5.00
             */
            minimum_incremental_profit: string;
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
        /**
         * ReproducibilityResponse
         * @description What this analysis was computed against — spec §57's record, whole.
         *
         *     Every field is read from the row rather than resolved here. That is the
         *     point of the record: the versions are the ones that were in force when the
         *     run claimed the analysis, and a value worked out at read time would describe
         *     whichever versions happen to be current now. They are written once and a
         *     database trigger refuses to change them, so re-reading this a year later
         *     answers what it answered on the day.
         *
         *     Two of §57's eight fields are not here because they are already elsewhere in
         *     the response: `analysis_id` is the analysis's own `id`, and the input image
         *     hashes are :attr:`image_sha256`.
         *
         *     A null is a documented absence, never an omission. Each field below says
         *     what its own null means, because "no model bundle exists yet" and "the field
         *     was not sent" must not look the same to a reader a year from now.
         */
        ReproducibilityResponse: {
            /**
             * Application Version
             * @description The version of this service that ran the analysis. Null until a run has claimed it — which is also the marker that no reproducibility record has been written yet. Deliberately not the version that opened the session: a session lives for days, and a deployment can happen inside one.
             * @example 0.1.0
             */
            application_version: string | null;
            /**
             * Card Database Version
             * @description The published card catalog the analysis ran against, as the identifier `GET /catalog/version` reports — captured at execution time, never a pointer to whatever is current now. Null when no run has claimed the analysis, or when no catalog version had been published when one did.
             * @example pokemon-catalog-v0.3.0
             */
            card_database_version: string | null;
            /**
             * Economic Configuration Id
             * @description The fee and cost configuration used. Always null in V1: the economic engine arrives with its own milestone.
             */
            economic_configuration_id: string | null;
            /**
             * Grading Rules Version
             * @description The grading-rule version the prediction was made under. Always null in V1: no grading rules exist yet.
             */
            grading_rules_version: string | null;
            /**
             * Image Sha256
             * @description §57's input image hashes, by side — the digest of the bytes that were *stored*, computed at upload. Empty before the first upload. A photograph cannot be replaced once an analysis has left `uploaded`, so these no longer change by the time a run records the rest of this.
             */
            image_sha256: {
                [key: string]: string;
            };
            /**
             * Market Snapshot Id
             * @description The pre-ingested market snapshot the economics were computed against. Always null in V1: market data arrives with its own milestone.
             */
            market_snapshot_id: string | null;
            /**
             * Model Bundle Version
             * @description The model bundle that produced the condition and grade predictions. Always null in V1: no model exists yet, and an explicit identifier — never `/latest/` — is what will go here when one does (spec §31).
             */
            model_bundle_version: string | null;
        };
        /**
         * ResultsResponse
         * @description The body of `GET /analyses/{analysis_id}/results` — spec §64, §6, §41, §44.
         */
        ResultsResponse: {
            /**
             * Analysis Id
             * Format: uuid
             */
            analysis_id: string;
            /**
             * Card Id
             * @description The confirmed card, or `null` before confirmation.
             */
            card_id: string | null;
            /**
             * Companies
             * @description One entry per configured company. **Empty until a grade distribution exists** — no milestone predicts one yet, so this is `[]` today, and it is empty rather than absent so a client parses the same shape either way.
             */
            companies: components["schemas"]["CompanyEconomicsResponse"][];
            /**
             * Currency
             * @description ISO 4217 code for every amount below.
             * @example SGD
             */
            currency: string;
            /** @description What the economics were computed under, or `null` if none was supplied. */
            economic_configuration: components["schemas"]["EconomicConfigurationResponse"] | null;
            /** @description The snapshot recorded on this analysis, or `null` when nothing had been ingested when it ran. */
            market_snapshot: components["schemas"]["MarketSnapshotReference"] | null;
            /** @description Spec §44's answer, or `null` when the analysis has not been calculated. **`null` is not `insufficient_information`**: the first means nobody has asked yet, the second that we asked and the data did not support an answer. */
            recommendation: components["schemas"]["RecommendationResponse"] | null;
            /**
             * Status
             * @description The analysis's state, so a client can tell 'not finished yet' from 'we could not tell'. Spec §65's states; poll `GET /analyses/{id}` for it.
             */
            status: string;
        };
        /**
         * SellingFeeRequest
         * @description Spec §46's `selling_fee`: a proportion of the sale price, plus a flat part.
         */
        SellingFeeRequest: {
            /**
             * Flat
             * @description The fixed part, charged per sale regardless of price.
             * @example 40.00
             */
            flat?: number | string;
            /**
             * Rate
             * @description The proportion of the realised sale price taken as commission. **A proportion in [0, 1], never a percentage**: ten percent is `"0.10"`, and `"10"` is refused rather than silently read as 1000%.
             * @example 0.10
             */
            rate?: number | string;
        };
        /** SellingFeeResponse */
        SellingFeeResponse: {
            /**
             * Flat
             * @description The fixed part, per sale.
             * @example 0.00
             */
            flat: string;
            /**
             * Rate
             * @description A proportion in [0, 1], as a decimal string.
             * @example 0.1000
             */
            rate: string;
        };
        /**
         * StoredMarkerResponse
         * @description One marker as it was stored.
         *
         *     Flat, where the request is a tagged union: the three kinds differ in what they
         *     *may* say, and a stored row has already said it. `region` is null for a
         *     surface, which is the same fact the union expresses by omitting the field.
         */
        StoredMarkerResponse: {
            /**
             * Annotator Id
             * @description Who recorded it — supplied by the service, never by a client.
             */
            annotator_id: string;
            /** @description §17's spatial data. */
            bbox?: components["schemas"]["BoundingBoxModel"] | null;
            /**
             * Confidence
             * @description How sure the annotator was.
             */
            confidence: number;
            /**
             * Created At
             * Format: date-time
             * @description §30's annotation timestamp.
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             * @description The annotation's identifier.
             */
            id: string;
            /**
             * Kind
             * @description Corner, edge or surface.
             */
            kind: string;
            /**
             * Label
             * @description What was found.
             */
            label: string;
            /**
             * Region
             * @description Where on the card, null for a surface.
             */
            region?: string | null;
            /**
             * Severity
             * @description How bad, null where nothing was rated.
             */
            severity?: string | null;
        };
        /**
         * StoredMeasurementResponse
         * @description One centering measurement as it was stored.
         */
        StoredMeasurementResponse: {
            /**
             * Annotator Id
             * @description Who recorded it.
             */
            annotator_id: string;
            /**
             * Confidence
             * @description How sure the annotator was.
             */
            confidence: number;
            /**
             * Created At
             * Format: date-time
             * @description §30's annotation timestamp.
             */
            created_at: string;
            /**
             * Horizontal
             * @description left / (left + right).
             */
            horizontal?: number | null;
            /**
             * Id
             * Format: uuid
             * @description The measurement's identifier.
             */
            id: string;
            /**
             * Notes
             * @description The annotator's note.
             */
            notes?: string | null;
            /**
             * Vertical
             * @description top / (top + bottom).
             */
            vertical?: number | null;
        };
        /**
         * SurfaceLabel
         * @description Spec §16's potential surface classes, in the specification's order.
         *
         *     Twelve, and **no `clean`** — see the module docstring. Where a corner is
         *     annotated once and may be found sound, a surface carries one annotation per
         *     defect found and none at all when there are none.
         * @enum {string}
         */
        SurfaceLabel: "scratch" | "print_line" | "dent" | "indentation" | "stain" | "scuff" | "print_dot" | "color_issue" | "registration_issue" | "gloss_issue" | "factory_defect" | "unknown";
        /**
         * SurfaceMarkerRequest
         * @description One surface defect — spec §16.
         *
         *     **No region field at all**, because §16 names no positions: a surface defect's
         *     position is its bounding box. And no `clean`, which is the specification's:
         *     a surface with nothing wrong is a surface nobody annotated, where a corner
         *     inspected and found sound is a row saying so.
         */
        SurfaceMarkerRequest: {
            /** @description §17's spatial data, where the annotator drew it. Optional: a corner's region already names its position, so `top_left: clean` is a complete annotation. **Only meaningful against a stored artifact** — see the endpoint's 409. */
            bbox?: components["schemas"]["BoundingBoxModel"] | null;
            /**
             * Confidence
             * @description §30's uncertainty — how sure the annotator is of this call. **Required, with no default**: a default would read as certainty for every row nobody supplied one for, which is the fabricated confidence spec §2.7 forbids. The other half of the same rule is the `unknown` label every vocabulary carries.
             * @example 0.8
             */
            confidence: number;
            /**
             * @description Spec §30's surface defect annotation. (enum property replaced by openapi-typescript)
             * @enum {string}
             */
            kind: "surface";
            /**
             * @description §16's twelve potential classes.
             * @example scratch
             */
            label: components["schemas"]["SurfaceLabel"];
            /**
             * @description How bad it is — an **ordinal**, because there is one annotator and no agreement study, so finer granularity would record a precision nobody could reproduce. Null exactly when the label asserts no defect (`clean` found nothing to rate, `unknown` could not rate what it found), and required otherwise.
             * @example minor
             */
            severity?: components["schemas"]["DefectSeverity"] | null;
        };
        /** UnrankedCompanyResponse */
        UnrankedCompanyResponse: {
            /** Company */
            company: string;
            /**
             * Reason
             * @description Why this company has no place in the order. **It is unranked, not last** — a sentinel sorted to the bottom would read as 'the worst company', which is a claim nobody computed.
             */
            reason: string;
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
            /** @description Too many requests from this client (spec §55). Carries `Retry-After`. Outside the spec §66 taxonomy, which has no code meaning 'throttled' — see ADR 0005. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
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
    confirm_card_analyses__analysis_id__confirm_card_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description The identifier `POST /analyses` answered with. */
                analysis_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CardConfirmationRequest"];
            };
        };
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
            /** @description No analysis is recorded under that identifier — for this caller — or no card is recorded under the one in the body. The first is the bare 404 `GET /analyses/{id}` answers with; the second carries the spec §66 envelope with `card_not_identified`. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The analysis is not waiting for a confirmation. A bare body while it has merely not got there yet — outside the spec §66 taxonomy, which has no code meaning 'conflict' — and the §66 envelope once it has `failed`, carrying `image_quality_failure` when the gate refused the photographs and `analysis_failed` otherwise. The difference is whether trying again could ever help. */
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
            /** @description Too many requests from this client (spec §55). Carries `Retry-After`. Outside the spec §66 taxonomy — see ADR 0005. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
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
            /** @description The analysis store or the card catalog could not be reached. */
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
    configure_economics_analyses__analysis_id__economic_configuration_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description The identifier `POST /analyses` answered with. */
                analysis_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EconomicConfigurationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EconomicConfigurationResponse"];
                };
            };
            /** @description No analysis is recorded under that identifier — for this caller. The bare 404 `GET /analyses/{id}` answers with. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The analysis is not ready for a configuration, or already has one. Outside the spec §66 taxonomy, which has no code meaning 'conflict'. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The configuration is malformed — a negative amount, a selling-fee rate outside [0, 1], an unknown grading company or an unknown optimization mode. FastAPI's own validation body: spec §66 has no code for a malformed request, and forcing one would be a lie in the field callers trust. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Too many requests from this client (spec §55). Carries `Retry-After`. Outside the spec §66 taxonomy — see ADR 0005. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
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
            /** @description The analysis store or the configuration store could not be reached. */
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
    upload_image_analyses__analysis_id__images_post: {
        parameters: {
            query: {
                /** @description Which view of the card this photograph is. */
                side: "front" | "back";
            };
            header?: never;
            path: {
                /** @description The identifier `POST /analyses` answered with. */
                analysis_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "image/jpeg": string;
                "image/png": string;
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ImageResponse"];
                };
            };
            /** @description The upload is not an image this service accepts. Always `invalid_image`; the message says which rule was broken and nothing about how the decoder failed. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description No analysis is recorded under that identifier — for this caller. The same body `GET /analyses/{id}` answers with, for the same reason. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The analysis has moved past the point where its images can change. Outside the spec §66 taxonomy, which has no code meaning 'conflict'. */
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
            /** @description Too many requests from this client (spec §55, which names image uploads explicitly). Carries `Retry-After`. Outside the spec §66 taxonomy — see ADR 0005. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
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
            /** @description The analysis store or the image store could not be reached. */
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
    read_results_analyses__analysis_id__results_get: {
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
                    "application/json": components["schemas"]["ResultsResponse"];
                };
            };
            /** @description No analysis is recorded under that identifier — for this caller. The bare 404 `GET /analyses/{id}` answers with. */
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
            /** @description The analysis store, the configuration store or the market snapshot store could not be reached. */
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
            /** @description Too many requests from this client (spec §55). Carries `Retry-After`. Outside the spec §66 taxonomy, which has no code meaning 'throttled' — see ADR 0005. */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
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
    read_card_market_cards__card_id__market_get: {
        parameters: {
            query?: {
                /** @description Read a specific snapshot instead of the current one — spec §57's reproducibility, so a past analysis can be re-read exactly. Omit it for today's prices. */
                snapshot_id?: string | null;
            };
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
                    "application/json": components["schemas"]["CardMarketResponse"];
                };
            };
            /** @description Either no card is recorded under that identifier — `card_not_identified`, with `details.card_id` — or no snapshot was generated under the one `?snapshot_id=` named, which is `market_data_unavailable` with `details.snapshot_id`. */
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
            /** @description `market_data_unavailable` when nothing has been ingested yet, so there is no snapshot to read. `provider_error` when a store could not be reached, with `details.reason` of `market_store_unreachable` or `catalog_unreachable`. */
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
    list_grading_companies_grading_companies_get: {
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
                    "application/json": components["schemas"]["GradingCompaniesResponse"];
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
            /** @description The grading rules could not be read. `details.reason` is `grading_rules_unreachable`. */
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
    list_images_awaiting_annotation_internal_annotation_images_get: {
        parameters: {
            query?: {
                /** @description How many images to return. */
                limit?: number;
                /** @description How many to skip. */
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
                    "application/json": components["schemas"]["AnnotationWorkListResponse"];
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
            /** @description The corpus could not be read. */
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
    read_training_image_internal_annotation_images__image_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description The training image's identifier. */
                image_id: string;
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
                    "application/json": components["schemas"]["AnnotationImageResponse"];
                };
            };
            /** @description No such training image. */
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
            /** @description The corpus could not be read. */
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
    record_image_annotations_internal_annotation_images__image_id__annotations_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description The training image being annotated. */
                image_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AnnotationRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AnnotationResponse"];
                };
            };
            /** @description No such training image. A bare body, deliberately outside the spec §66 envelope: none of the eight codes means 'not found'. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The image has no stored artifact, and the annotation carries coordinates that would be fractions of one. Also a bare body — §66 has no code for a conflict, and a ninth is not invented for this. */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description The annotation is not one the schema would take: a label outside its kind's list, a region on a surface or missing from a corner, a defect with no severity or a `clean` with one, a box outside the unit square, a measurement of neither axis, or a request recording nothing at all. FastAPI's own validation body — §66 has no code for a malformed request. */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
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
            /** @description The training image corpus could not be reached. */
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
    read_training_image_bytes_internal_annotation_images__image_id__bytes_get: {
        parameters: {
            query?: {
                /** @description Which representation to serve. */
                representation?: "normalized" | "original";
            };
            header?: never;
            path: {
                /** @description The training image's identifier. */
                image_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The stored bytes. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "image/jpeg": unknown;
                    "image/png": unknown;
                };
            };
            /** @description No such training image, or no such representation of it. */
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
            /** @description The corpus or the image store could not be reached. */
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
