import sys
from common import is_in_alert_period1
if __name__ == '__main__':
    start = sys.argv[1]
    end = sys.argv[2]
    current = sys.argv[3]
    print is_in_alert_period1(start, end, current)