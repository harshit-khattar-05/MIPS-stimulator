#include<iostream>
using namespace std;
int main() {
    int A[10] = {5,2,7,3,1,4,6,8,9,10};
    int P[10];

    P[0] = A[0];

    for(int i = 1; i < 10; i++) {
        P[i] = P[i-1] ^ A[i];
    }

    for(int i = 0; i < 10; i++) {
        printf("%d ", P[i]);
    }
    return 0;
}