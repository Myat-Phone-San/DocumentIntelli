import './globals.css';

export const metadata = { title: 'Intelligence · Your document assistant', description: 'Ask better questions. Find answers in approved documents.' };

export default function RootLayout({ children }) {
  return <html lang="en"><body>{children}</body></html>;
}
