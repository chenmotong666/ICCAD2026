module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n12,
    n30,
    n20,
    n22,
    n21,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  input [3:0] n12;
  output [1:0] n30;
  output n20;
  output n22;
  output n21;
  output n23;

  wire \$and$testcase/test49/test49.v:11$4_Y , \$or$testcase/test49/test49.v:12$6_Y , \$xor$testcase/test49/test49.v:13$8_Y , n100, n101, n102, n103, n104, n105, n107, n108, n114, n120, n121, n122, n123, n128, n129, n130, n131;

  and   \$and$testcase/test49/test49.v:15$10  (n107, n2, n3);
  and   \$and$testcase/test49/test49.v:29$20  (n120, n2, n3);
  and   \$and$testcase/test49/test49.v:7$1  (n100, n2, n3);
  and   \$and$testcase/test49/test49.v:30$21  (n121, n2, n4);
  not   \$not$testcase/test49/test49.v:23$35  (n114, n4);
  and   \$and$testcase/test49/test49.v:31$22  (n122, n2, n5);
  and   \$and$testcase/test49/test49.v:32$23  (n123, n2, n6);
  xor   \$xor$testcase/test49/test49.v:40$29  (n131, n12[0], n12[1]);
  or    \$or$testcase/test49/test49.v:42$31  (n30[1], n12[2], n12[3]);
  or    \$or$testcase/test49/test49.v:16$11  (n108, n107, n5);
  or    \$or$testcase/test49/test49.v:8$2  (n101, n100, n4);
  or    \$or$testcase/test49/test49.v:33$24  (n128, n120, n121);
  or    \$or$testcase/test49/test49.v:25$15  (n22, n114, n100);
  xor   \$xor$testcase/test49/test49.v:34$25  (n129, n122, n123);
  xor   \$xor$testcase/test49/test49.v:17$12  (n21, n108, n6);
  not   \$not$testcase/test49/test49.v:9$32  (n102, n101);
  and   \$and$testcase/test49/test49.v:39$28  (n130, n129, n128);
  xor   \$xor$testcase/test49/test49.v:10$3  (n103, n102, n5);
  and   \$and$testcase/test49/test49.v:41$30  (n30[0], n131, n130);
  and   \$and$testcase/test49/test49.v:11$4  (\$and$testcase/test49/test49.v:11$4_Y , n103, n6);
  not   \$not$testcase/test49/test49.v:11$5  (n104, \$and$testcase/test49/test49.v:11$4_Y );
  or    \$or$testcase/test49/test49.v:12$6  (\$or$testcase/test49/test49.v:12$6_Y , n104, n7);
  not   \$not$testcase/test49/test49.v:12$7  (n105, \$or$testcase/test49/test49.v:12$6_Y );
  xor   \$xor$testcase/test49/test49.v:13$8  (\$xor$testcase/test49/test49.v:13$8_Y , n105, n8);
  not   \$not$testcase/test49/test49.v:13$9  (n20, \$xor$testcase/test49/test49.v:13$8_Y );

  assign n23 = n5;

endmodule
