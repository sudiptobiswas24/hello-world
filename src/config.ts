import type { Layout } from './engine';

/**
 * App-level presentation settings (the design exposed these as "tweaks").
 */
export const APP_CONFIG: {
  /** calculator list layout used until the user picks one */
  defaultLayout: Layout;
  /** show the production-days strip under each cost sheet */
  showPlanStrip: boolean;
  /** company name printed on quotes and job cards */
  companyName: string;
} = {
  defaultLayout: 'sheet',
  showPlanStrip: true,
  companyName: 'Bellwort Papers Pvt Ltd',
};
