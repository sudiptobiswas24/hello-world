export function calcGst(taxableValue: number, gstRate: number) {
  const igst = taxableValue * gstRate / 100
  const cgst = igst / 2
  const sgst = igst / 2
  return { taxableValue, gstRate, igst, cgst, sgst, total: taxableValue + igst }
}
export function isInterState(fromState: string, toState: string) {
  return fromState !== toState
}
export function gstRateFromHsn(hsn: string): number {
  // PP Woven Sacks typically 18% GST
  if (hsn.startsWith('6305')) return 12
  if (hsn.startsWith('3920') || hsn.startsWith('3921')) return 18
  return 18
}
