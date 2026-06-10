public class TestFFT {
    public static void main(String[] args) {
        int size = 8;
        int log2N = 3;
        int[] bitReversalTable = new int[size];
        for (int i = 0; i < size; i++) {
            int rev = 0;
            int temp = i;
            for (int j = 0; j < log2N; j++) {
                rev = (rev << 1) | (temp & 1);
                temp = temp >>> 1;
            }
            bitReversalTable[i] = rev;
        }

        float[] cosTable = new float[size / 2];
        float[] sinTable = new float[size / 2];
        for (int k = 0; k < size / 2; k++) {
            double angle = 2.0 * Math.PI * k / size;
            cosTable[k] = (float) Math.cos(angle);
            sinTable[k] = (float) Math.sin(angle);
        }

        float[] real = new float[size];
        float[] imag = new float[size];
        real[1] = 1.0f;

        // 1. Bit-reversal sorting
        for (int i = 0; i < size; i++) {
            int j = bitReversalTable[i];
            if (i < j) {
                float tempRe = real[i];
                real[i] = real[j];
                real[j] = tempRe;

                float tempIm = imag[i];
                imag[i] = imag[j];
                imag[j] = tempIm;
            }
        }

        // 2. Butterfly stages
        int len = 2;
        while (len <= size) {
            int halfLen = len / 2;
            int twiddleStep = size / len;

            for (int i = 0; i < size; i += len) {
                for (int j = 0; j < halfLen; j++) {
                    int k = i + j;
                    int l = k + halfLen;

                    int twiddleIdx = j * twiddleStep;
                    float wr = cosTable[twiddleIdx];
                    float wi = -sinTable[twiddleIdx]; // forward = true

                    float tRe = real[l] * wr - imag[l] * wi;
                    float tIm = real[l] * wi + imag[l] * wr;

                    real[l] = real[k] - tRe;
                    imag[l] = imag[k] - tIm;

                    real[k] += tRe;
                    imag[k] += tIm;
                }
            }
            len = len << 1;
        }

        for (int i = 0; i < size; i++) {
            System.out.printf("bin %d: re=%.6f, im=%.6f\n", i, real[i], imag[i]);
        }
    }
}
