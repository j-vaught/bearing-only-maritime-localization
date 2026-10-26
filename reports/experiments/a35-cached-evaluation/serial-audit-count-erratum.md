# A35 serial audit count correction

The frozen serial expansion event file contains 20,435 branch starts, 20,435 branch results, and one failure event. The 20,435 results comprise 20,381 results in completed modes 0–59 and 54 results in the incomplete mode-60 tail.

The completed rows contain 12,150 exact-score results, 8,225 `invalid_water` results, and six `max_nfev` results. The incomplete mode-60 tail contains 54 exact-score results. The prior A35 report counted all 12,204 exact-score event payloads as rescored, but the evaluator's canonical replay loop processes only completed rows 0–59. Therefore, the demonstrated canonical replay count is 12,150. The 54 mode-60 exact-score results were preserved and structurally checked, but were not independently rescored in that pass.

This erratum corrects the interpretation only. The original serial JSON, event file, manifest, and hashes remain unchanged. The parallel composite audit will replay all parallel mode-60 through mode-63 exact-score branch poses, including the previously unchecked mode-60 tail.

The correction is bound to serial manifest SHA-256 `290c2645f8252a951491a99e2fa6bca6b29d2222d04cec8c9935a75b27db5e23`, event file SHA-256 `a92acc79294bd045605f5bcce9e76e26ab7ccddc03ccba1ace0df69eb982b7bf`, and the immutable A35 report JSON SHA-256 `7111b39f72caf0df53e655681cb4460118b781a08bbbafdd396056774523bbf8`.
