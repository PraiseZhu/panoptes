// QA misattribution fix (Praise PR step 9) rollback switch, re-read on every call (no restart needed):
// the file $QA_HOME/qa-misattrib-off (default /workspace/mivo-qa/qa-misattrib-off) exists, or PANOPTES_QA_MISATTRIB_OFF=1
// → old behaviour: bare FAIL lines to code review, review "needs evidence" items as their own pending entry, no test-script flag,
// no forced reduced motion, AI tests run before the mutation check.
import { existsSync } from 'node:fs'
import { join } from 'node:path'

export const qaMisattribOffFile = (env = process.env) => join(env.QA_HOME || '/workspace/mivo-qa', 'qa-misattrib-off')
export const qaMisattribOff = (env = process.env) => env.PANOPTES_QA_MISATTRIB_OFF === '1' || existsSync(qaMisattribOffFile(env))
