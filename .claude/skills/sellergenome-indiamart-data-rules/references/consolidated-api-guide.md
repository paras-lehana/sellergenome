<!-- Markdown copy of the owner-supplied 'Persona Generator Consolidated API Guide.docx' (2026-10-09), kept
     verbatim for agents. Original: docs/tasks/2026-10-10-persona-generator-api-guide/. Do not edit — add notes in SKILL.md. -->

# Persona Generator: Consolidated API Guide

Oct 9, 2026

## How the APIs fit together

Five APIs feed the persona generator. This guide lists only keys whose meaning is clear from the samples or confirmed by the team. Anything uncertain is left out and listed at the end.

The UI has three sections: 1 Basic details, 2 Engagement with IndiaMART, 3 Engagement with buyers.

| API | What it gives | Feeds | Seller id in the response |
|---|---|---|---|
| Global Context | One bundle per seller: profile, calls and meetings of the last 30 days, tickets, platform activity, buyer counts, demand | Sections 1, 2 and 3, and language | glid, at the top and in each bundle |
| STS History | A dated log of updates on the seller's tickets, calls and meetings | Section 2, and the evidence for past-call rules | None. The request identifies the seller. |
| PNS Details | Analysed buyer phone calls to the seller | Section 3, and language | SELLER_GLID |
| Enquiry | Buyer enquiries sent to the seller | Section 3 | None. The request identifies the seller. |
| Company Attribute | The seller's public storefront: profile, catalogue, ratings | Section 1, and the catalogue part of section 3 | GLUSRID in COMPANYDETAIL |

The seller id across all five APIs is the glid. The STS History and Enquiry responses do not carry it, so attach the glid you requested to each response before joining.

## Rules that apply to every API

- Evidence. Every insight cites the key path or the record it came from. No evidence, no insight.

- Confidence.

- High: a recorded value, such as a count, date or status.

- Medium: read from free text (a note, summary or transcript) by one source.

- Guess: inferred from missing or indirect data.

- Conflicts. When records disagree or a field is empty, show it as a doubt and lower the confidence. Do not choose silently.

- Persona. It changes delivery only. Facts, pricing and compliance statements never change. The bot never claims to be human.

- Language. No API has a language field. Read it from the seller's own words in call transcripts (the "Customer:" lines in the Global Context API) and from primary_language and all_languages in the PNS Details API. Fall back to the language of his city and state, marked Guess.

Never mention, on a call or in a persona

- Buyer names, phone numbers, ids, the city of an individual buyer, quotes from buyer calls or enquiries, and recording links.

- The seller's own prices, turnover figures or any exact amount.

- GST number, address, email, phone numbers and map coordinates.

- Employee ids and the names of IndiaMART staff.

- Buyer-seller conflict tickets, refunds, legal or operations tickets, complaint counts, fraud or risk flags, and raw ticket history.

- Direct quotes from transcripts.

- Competitor names, one-star ratings and the star split.

- Enquiries asking for "First Copy" or other counterfeit goods.

- Any claim that he ignored an enquiry or missed a follow-up that the data does not show.

## Hot lead types

The NSD bot belongs to the New Sales Division and calls free sellers. The hot lead type is the reason for the call, so it sets the opening reason. These meanings are as given by the team.

| Code | Meaning |
|---|---|
| SCHD | Schedule a demo hotlead |
| PIM | Payment Interested (activity) |
| PAM | Payment Attempted (activity) |
| OLP | Online Payment (activity) |
| OLPR | Online Payment Renewal (activity) |
| PUA | Premium User Activity |
| UA | User Activity |
| TF | Toll-Free (related to supplier calls) |
| UATF | Unassigned Toll-Free, implied from context with TF (the team's note) |

PNSM appears on one vendor payload but is not in the team's list, so it is left out.

## Global Context API

One response per seller (glid) with five sections the generator reads: kycdetails, connectdetails, ticketsdetails, activitydetails and BLdetails. Paths start under data. The NSD bot uses 10 of these today, per the master sheet: company name, username, hl_pns_received_30d, hl_pns_success_30d, local_bl, total_bl, top3mcat_data, top3mcat_competitors, enq_last_30_days and glusr_custtype_id.

Profile

| Key | Meaning | Format |
|---|---|---|
| kycdetails.company_name | Company name | Text |
| kycdetails.customer_name, first_name | Contact on the account | Text |
| kycdetails.city | City | Text |
| kycdetails.gst_turnover | Turnover band | Text like "5 - 25 Cr". "NA" means missing. |
| kycdetails.gst_nature_of_business | Business type from GST | Text |
| kycdetails.company_type | Legal form | LLP, Proprietor and so on |
| kycdetails.registered_since_with_indiamart | Time on IndiaMART | Text like "25 Y 1 M" |
| kycdetails.customer_type | Paid or Free | Text |
| kycdetails.top_city_rest_of_india | Other cities where his buyers are | One comma-separated string |
| kycdetails.approved_product_count | Approved products | Number stored as text |
| kycdetails.payment_invoice_date, payment_receipt_date | Payment dates | Dates in different formats |
| kycdetails.last_call_details[] | Executive comments | COMMENTS and ENTERED_ON |

Calls, meetings and tickets

| Key | Meaning | Format |
|---|---|---|
| connectdetails.connect_7D[], connect_30D[] | One row per call or meeting | List |
| ...call_answered_date | Timestamp of the call row. Present even when the call was not picked up. | "2026-10-07 19:42:16" |
| ...call_answered_summary | Call summary | Text, often empty |
| ...call_answered_transcript | Call transcript | Text with "Customer:" and "Executive:" labels |
| ...call_sts_update | Executive's call note | Free text |
| ...meet_sts_update | Meeting note | Free text in a numbered template |
| ...meet_sucessful_date | Date the meeting was done. The key is spelled "sucessful". | Date |
| ...video_meet_sts_update, video_verified_date | Video meeting note and date | Same as meetings |
| connectdetails.last_call_summary | Latest call summary | Text, can be empty |
| ticketsdetails.current.tickets, last_7_days.tickets, last_8_to_90_days.tickets[] | Tickets in each window | List, or null |
| ...ticket_type, ticket_issue_date, ticket_opening_comments | Type, date and opening comment of a ticket | Text |

Activity, buyers and demand

| Key | Meaning | Format |
|---|---|---|
| activitydetails.enq_last_30_days, enq_last_90_days | Buyer enquiries reaching him | Integer |
| activitydetails.pns_received_30_days, pns_received_90_days | Buyer phone calls reaching him | Integer |
| activitydetails.last_week, last_4_week, last_month, last_3_month .bl_active_days | Days active on buy leads in that window | Integer |
| activitydetails.last_month, last_4_week .prd_without_photo, prd_without_price, prd_without_ctg_mapping | Products with each catalogue gap | Integer |
| activitydetails.location_preference | Where he wants leads from | List in last_4_week, string in last_month |
| activitydetails.top3mcat_data[0] | local_bl, total_bl, local_paid_sellers, total_paid_sellers, mcat_list | mcat_list is a JSON string |
| activitydetails.top3mcat_competitors[] | Competitor company, city, tier and member-since | Up to 10 rows |
| BLdetails.* | Buy leads available to him | Counts, and a list in active_bl_details |

Pointers

- Count meetings as rows with meet_sucessful_date filled. A call timestamp alone is not proof the call was answered. Check the note, summary or transcript and call status.

- meet_sts_update follows a template: 01 Meeting Agenda, 02 Decision Maker Name & No, 03 Major Products/Categories, 04 Meeting Summary & Work Done, 05 ROI/Business Generated in last 6 Months, 06 Work to be Done Post Meeting, 07 Comments/Pending work. Split on the numbers.

- Use tickets only to know an issue is open or provide or also check the resolution if it requires in output. 

## STS History API

This API returns the history log for a seller. The team says it holds the full history of calls and meetings, with a summary of every call, the disposition of each call and meeting, and the updates executives made. That makes it the source for the past-call evidence. The only sample so far is an automated one, so the call and meeting parts are not yet seen.

Format of human entries. The ticket history inside the Global Context API shows the lines to expect: "Call Update" with a disposition and a scheduled appointment, "Meeting Update" with a date, outcome and who attended, "TBRO Added" with a follow-up date, and "Video Meet Done".

Pointers

- Separate automated updates (updater named as a process) from human ones, and merge repeated automated entries into one with a date range.

- Treat the call summaries and dispositions as the outcome side of the evidence join. Link each rule to the seller type and the approach the executive used.

- Never mention the "Mail To" addresses or the names of staff.

- The response has no seller id. The request identifies the seller.

## PNS Details API

One analysed record for each buyer phone call that reached the seller. An AI model extracted each record from the call recording. The response is {Code, Status, UniqueId, data[], page_index}, and each data[] item is one call. In the table, metadata means extracted_data.metadata.

| Key | Meaning | Values seen |
|---|---|---|
| file_id, call_timestamp | Call id and when it happened | Integer; "2026-09-04 15:45:06" |
| SELLER_GLID | Seller id, the join key | Integer |
| BUYER_GLID | Buyer id. Use only to count distinct buyers. | Integer |
| mcat_name | Product category of the call | Text, can be null |
| metadata.call_purpose | Why the call happened | requirement, followup, irrelevant |
| metadata.buyer_intent.intent_level | Buyer's buying intent | High, Medium, Low |
| lead_tag.deal_readiness | How ready the deal is | Warm, Cold |
| lead_tag.deal_blockers[] | What blocks the deal | category and detail |
| metadata.call_outcome | Outcome and a note | category Follow-up or Lost, and conclusion_notes |
| metadata.call_type | Business or consumer buyer, with evidence | type B2B or B2C. Evidence holds the buyer persona, order type, quantity scale and repeat buyer. |
| metadata.primary_language, all_languages | Languages spoken on the call | Text and a list |
| additional_details.buyer_queries[], seller_queries[] | What each side asked | Query text with a category |
| next_steps, callback | Agreed next actions and any callback promised | Lists of text, and a boolean or text |
| products[] | Products discussed | Name, in_stock, quantity and price when mentioned |
| seller_details | Seller name as heard, and city | Text, can be empty |

Pointers

- A callback in a record shows a promise, not that it was kept. Say only that a callback was promised.

- Language counts support the language dial, alongside the transcripts.

- Resolve relative dates such as "Kal" against call_timestamp.

Cautions

- The data is AI-extracted, so records can disagree. In the sample, three calls with one buyer within a minute conflict on whether the product is in stock. Trust patterns over a single record.

- Several records can be one conversation. Count distinct buyers and days.

- Only 1 of 10 buyer queries in the sample has a recorded answer from the seller, so skip any insight on how responsive he is.

- Never use buyer names, numbers, quoted words, prices or the recording link.

## Enquiry API

The buyer enquiries that reached a seller, newest first. The response is {status, code, error, response}, and the enquiries are in response.enquiries[]. It shows what arrived, not what the seller did with it. The response has no reply status.

| Key | Meaning | Values seen |
|---|---|---|
| enquiryTitle | Product the buyer asked for | Text |
| enquiryISQ[] | The buyer's answers to specification questions | List of {DESC, RESPONSE}, can be empty. Includes "Quantity" and "Quantity Unit". |
| postedDate | When the enquiry was posted | ISO with microseconds, no timezone |
| buyerName | Buyer name or company | Text with trailing spaces. Never shown. |
| buyerMemberSince | When the buyer joined IndiaMART | ISO date-time |
| mobileVerified, emailVerified, addressVerified, gstVerified | Buyer verification checks | 0 or 1 |
| buyerCity, buyerState | Buyer location | Text, can be empty |
| countryFlag | Image link for the buyer's country | in_flag_s.png is India, de_flag_s.png is Germany |
| queryId | Enquiry id | Integer |

Pointers

- Count enquiries in the last 7 and 30 days from postedDate. The list may be capped, so take the true total from enq_last_30_days in the Global Context API.

- Identify a buyer by trimmed buyerName plus buyerMemberSince, and count distinct buyers.

- Buyer quality is how many of the four verification checks pass, and whether the buyer is new or established.

- Seriousness is whether a quantity is given.

- Use titles and specs only for the category. In the sample they contradict each other, for example a title of one phone model with another model in the specs.

- Skip and never mention any enquiry that asks for a "First Copy" quality grade. IndiaMART does not allow counterfeit goods.

- Empty cities, empty spec lists and a foreign country flag are normal, not errors.

## Company Attribute API

The seller's public storefront on IndiaMART. It shows what kind of business he runs and how complete his profile and catalogue are. The response has three top-level keys: DATA, URL_DETAIL and STANDARD_PRODUCTS. URL_DETAIL sits beside DATA, not inside it.

| Key | Meaning | Values seen |
|---|---|---|
| DATA.COMPANYDETAIL.DIR_SEARCH_COMPANY | Company name | Text |
| DATA.COMPANYDETAIL.DIR_SEARCH_CONTACTPERSON, DIR_SEARCH_DESIGNATION | Contact person and role | Text, for example "owner" |
| DATA.COMPANYDETAIL.BIZ, LEGALSTATUS | Nature of business and legal form | "Service Provider"; "Individual - Proprietor" |
| DATA.COMPANYDETAIL.NUMBEROFEMP_VAL | Staff size band | "11 to 25 People" |
| DATA.COMPANYDETAIL.YOE | Year established | "2023" as text |
| DATA.COMPANYDETAIL.DIR_SEARCH_CITY, DIR_SEARCH_STATE | Location | Text |
| DATA.COMPANYDETAIL.GLUSRID | Seller id, the join key | Integer |
| DATA.FACTSHEET[] | Factsheet rows with a title and a value | Includes "Annual Turnover" and "GST Turnover" |
| DATA.PRD_COUNT | Total listings | Integer |
| DATA.PRDNAV[] | Categories with CAT_NAME, CAT_PRD_COUNT and a PRODLIST[] of items with ITEM_NAME and ISQ | Counts add up to PRD_COUNT. PRODLIST can hold fewer items. |
| DATA.PRDSERV[] | Featured items with ITEM_NAME, PC_ITEM_PRICE_F, PC_ITEM_VIDEO_PATH and ISQ | Price like "₹ 99/sq ft" |
| DATA.HEADER.DOWNLOAD_BROCHURE, TESTIMONIALS, GALLERY_LINK | Brochure file, testimonial page and photo page | File or page name, or empty |
| DATA.HOMECORPORATEVIDEO, URL_DETAIL.COMP_TAGLINE | Company video and tagline | Empty when not set |
| URL_DETAIL.ABOUTPAGE_DESC | About-us text | HTML string |
| DATA.SUPPLIER_RATING, RATING_PERCENTAGES | Overall rating, count and star split | "4.0", 28 ratings, "61%" five-star |
| URL_DETAIL.PRIVACY_SETTINGS | Switches such as allow_enquiry_generation and allow_pns_call | "enabled" or "disabled" |
| DATA.PREF_COUNTRIES, TOP_EXPORT_COUNTRIES | Export countries | Lists, empty in the sample |
| URL_DETAIL.URL_TYPE | Showroom type | "PAID" seen. The free value is not yet seen. |
