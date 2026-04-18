export type ChartTone = 'rates' | 'green' | 'blue' | 'amber' | 'neutral' | 'coral';

export function chartStrokeForTone(tone: ChartTone) {
  switch (tone) {
    case 'rates':
      return '#D4B08B';
    case 'green':
      return '#3FD69A';
    case 'blue':
      return '#7AA2FF';
    case 'amber':
      return '#F3B755';
    case 'coral':
      return '#FF6B7E';
    default:
      return '#A4A8B6';
  }
}

export function chartFillForTone(tone: ChartTone) {
  switch (tone) {
    case 'rates':
      return 'rgba(212, 176, 139, 0.18)';
    case 'green':
      return 'rgba(63, 214, 154, 0.18)';
    case 'blue':
      return 'rgba(122, 162, 255, 0.18)';
    case 'amber':
      return 'rgba(243, 183, 85, 0.18)';
    case 'coral':
      return 'rgba(255, 107, 126, 0.18)';
    default:
      return 'rgba(164, 168, 182, 0.14)';
  }
}
