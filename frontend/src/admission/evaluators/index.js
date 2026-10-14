/** Every evaluator, keyed by the `evaluator` id a rule file names. */
import * as grade1 from './bg/grade1.js'
import * as grade8 from './bg/grade8.js'
import * as sofiaKindergarten from './bg/sofiaKindergarten.js'

export const EVALUATORS = {
  'bg/grade1': grade1,
  'bg/grade8': grade8,
  'bg/sofia/kindergarten': sofiaKindergarten,
}
